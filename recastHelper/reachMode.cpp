#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#define NOMINMAX
#include <windows.h>
#include <psapi.h>

#include "ChunkyTriMesh.h"
#include "Recast.h"
#include "helperIO.h"

namespace {

const char* reachMagic = "ZWREACH1";
const char* laneMagic = "ZWLANE01";
const int trianglesPerChunk = 512;
// Recast keeps a span's heights in 13 bits (8,191 cells); a taller zone is rasterized in slabs of this many cells.
const int slabCells = 8000;
const int tileBorder = 1;
const uint16_t noFloor = 0;
const uint16_t openSky = 0xFFFF;
const int highestCell = 0xFFFE;
const long long mostColumns = 600000000LL;
const uint16_t noSeed = 0xFFFF;
const uint32_t unclaimed = 0xFFFFFFFF;
// Frontiers shorter than this are flooded on one thread: the pool's hand-off costs more than they do.
const size_t parallelLevel = 4096;

enum Move : uint8_t {
  unreachedMove = 0, seedMove = 1, walkMove = 2, dropMove = 3, slideMove = 4, enterWaterMove = 5, swimMove = 6, climbOutMove = 7,
  sinkMove = 8, teleportMove = 9,
};
enum Shape : uint8_t { wetShape = 1, walkableShape = 2, absorbedShape = 4, touchesLineShape = 8 };
enum State : uint8_t { reachedFullState = 1, reachedState = 2, trapState = 4, fromSafeState = 8, toSafeState = 16, markState = 32, seedState = 64 };
const uint8_t laneStates = reachedFullState | reachedState | trapState | fromSafeState | toSafeState;
enum EventKind : uint8_t { voidEvent = 1, belowUnderworldEvent = 2, absorbedEvent = 3 };

struct Box {
  float low[3];
  float high[3];
};

struct ZoneLine {
  Box box;
  bool teleports;
  float target[3];
};

struct Seed {
  float point[3];
  float tolerance;
  std::string name;
};

struct ReachInput {
  float cellSize, cellHeight;
  int tileCells;
  float stepHeight, playerHeight, containmentHeadroom, walkableNormalZ;
  float footingAbove, footingBelow;
  bool hasUnderworld;
  float underworld;
  unsigned int threads;
  std::vector<float> vertices;
  std::vector<int> triangles;
  std::vector<Box> swimBoxes;
  std::vector<ZoneLine> zoneLines;
  std::vector<Seed> seeds;
  int safeSeed;
};

ReachInput readReachInput(const std::string& path) {
  InputReader reader(path, reachMagic);
  ReachInput input;
  input.cellSize = reader.readFloat();
  input.cellHeight = reader.readFloat();
  input.tileCells = reader.readInt32();
  input.stepHeight = reader.readFloat();
  input.playerHeight = reader.readFloat();
  input.containmentHeadroom = reader.readFloat();
  input.walkableNormalZ = reader.readFloat();
  input.footingAbove = reader.readFloat();
  input.footingBelow = reader.readFloat();
  input.hasUnderworld = reader.readUInt32() != 0;
  input.underworld = reader.readFloat();
  input.threads = reader.readUInt32();
  if (!(input.cellSize > 0) || !(input.cellHeight > 0) || input.tileCells < 8) {
    throw HelperError("the reach grid needs a positive cell size and height and tiles of at least 8 cells");
  }
  if (input.threads == 0) {
    throw HelperError("the reach input asks for 0 threads");
  }
  const uint32_t triangleCount = reader.readUInt32();
  if (triangleCount == 0) {
    throw HelperError("the reach input holds no collision triangles");
  }
  input.vertices.resize(static_cast<size_t>(triangleCount) * 9);
  reader.readFloats(input.vertices.data(), input.vertices.size());
  for (const float value : input.vertices) {
    if (!std::isfinite(value)) {
      throw HelperError("a collision triangle has a vertex that is not a finite number");
    }
  }
  input.triangles.resize(static_cast<size_t>(triangleCount) * 3);
  for (size_t index = 0; index < input.triangles.size(); ++index) {
    input.triangles[index] = static_cast<int>(index);
  }
  input.swimBoxes.resize(reader.readUInt32());
  for (Box& box : input.swimBoxes) {
    reader.readFloats(box.low, 3);
    reader.readFloats(box.high, 3);
  }
  input.zoneLines.resize(reader.readUInt32());
  for (ZoneLine& line : input.zoneLines) {
    reader.readFloats(line.box.low, 3);
    reader.readFloats(line.box.high, 3);
    line.teleports = reader.readUInt32() != 0;
    reader.readFloats(line.target, 3);
  }
  const uint32_t seedCount = reader.readUInt32();
  input.safeSeed = reader.readInt32();
  input.seeds.resize(seedCount);
  for (Seed& seed : input.seeds) {
    reader.readFloats(seed.point, 3);
    seed.tolerance = reader.readFloat();
    const uint32_t nameLength = reader.readUInt32();
    std::vector<unsigned char> name(nameLength);
    reader.readBytes(name.data(), nameLength);
    seed.name.assign(name.begin(), name.end());
  }
  reader.requireEnd();
  if (input.seeds.empty()) {
    throw HelperError("the reach input has no seeds");
  }
  if (input.seeds.size() >= noSeed) {
    throw HelperError("the reach input has " + std::to_string(input.seeds.size()) + " seeds, more than the lane records");
  }
  if (input.safeSeed >= static_cast<int>(input.seeds.size())) {
    throw HelperError("the safe point's seed index is past the seeds");
  }
  if (input.swimBoxes.size() > 32767) {
    throw HelperError("the reach input has more swim boxes than the lane records (32,767)");
  }
  return input;
}

class QuietContext : public rcContext {
public:
  QuietContext() : rcContext(true) {
    enableTimer(false);
  }

  std::string errors;

protected:
  void doLog(const rcLogCategory category, const char* message, const int length) override {
    if (category == RC_LOG_ERROR) {
      errors += (errors.empty() ? "" : "; ") + std::string(message, length);
    }
  }
};

struct Grid {
  float origin[3];
  float cellSize, cellHeight;
  int tileCells, tilesX, tilesZ, verticalCells, slabs;
  long long columns;

  long long columnOf(int x, int z) const {
    const long long tile = static_cast<long long>(z / tileCells) * tilesX + x / tileCells;
    return tile * tileCells * tileCells + static_cast<long long>(z % tileCells) * tileCells + x % tileCells;
  }

  void columnPlace(long long column, int& x, int& z) const {
    const long long perTile = static_cast<long long>(tileCells) * tileCells;
    const long long tile = column / perTile;
    const long long within = column % perTile;
    x = static_cast<int>((tile % tilesX) * tileCells + within % tileCells);
    z = static_cast<int>((tile / tilesX) * tileCells + within / tileCells);
  }

  int columnsX() const {
    return tilesX * tileCells;
  }

  int columnsZ() const {
    return tilesZ * tileCells;
  }

  float cellsAt(float y) const {
    return (y - origin[1]) / cellHeight;
  }
};

// A box in grid units: plan in columns, height in cells.
struct GridBox {
  float lowX, highX, lowZ, highZ, lowY, highY;
  int columnLowX, columnHighX, columnLowZ, columnHighZ;

  bool holdsColumn(int x, int z) const {
    return x >= columnLowX && x <= columnHighX && z >= columnLowZ && z <= columnHighZ;
  }
};

GridBox gridBox(const Grid& grid, const Box& box) {
  GridBox placed;
  placed.lowX = (box.low[0] - grid.origin[0]) / grid.cellSize;
  placed.highX = (box.high[0] - grid.origin[0]) / grid.cellSize;
  placed.lowZ = (box.low[2] - grid.origin[2]) / grid.cellSize;
  placed.highZ = (box.high[2] - grid.origin[2]) / grid.cellSize;
  placed.lowY = grid.cellsAt(box.low[1]);
  placed.highY = grid.cellsAt(box.high[1]);
  // Columns whose centers the box's plan holds, its faces included.
  placed.columnLowX = static_cast<int>(std::ceil(placed.lowX - 0.5f));
  placed.columnHighX = static_cast<int>(std::floor(placed.highX - 0.5f));
  placed.columnLowZ = static_cast<int>(std::ceil(placed.lowZ - 0.5f));
  placed.columnHighZ = static_cast<int>(std::floor(placed.highZ - 0.5f));
  return placed;
}

struct Span {
  int low, high;
  unsigned char area;
};

struct TileNodes {
  std::vector<uint32_t> counts;
  std::vector<uint16_t> bottomTop;
  std::vector<uint16_t> low, high, intervalLow, intervalHigh;
  std::vector<int16_t> box;
  std::vector<uint8_t> shape;
  std::string error;
};

struct Model {
  const ReachInput& input;
  Grid grid;
  int climb, playerCells, headroomCells;
  float underworldCells;
  std::vector<GridBox> swimBoxes, lineBoxes;
  std::vector<int> swimTops;
  std::vector<std::vector<uint8_t>> touching;
  std::vector<uint32_t> columnStart;
  std::vector<uint16_t> bottomTop;
  std::vector<uint16_t> low, high, intervalLow, intervalHigh;
  std::vector<int16_t> box;
  std::vector<uint8_t> shape;

  explicit Model(const ReachInput& source) : input(source) {}

  uint32_t nodeCount() const {
    return static_cast<uint32_t>(low.size());
  }

  bool walkable(uint32_t node) const {
    return (shape[node] & walkableShape) != 0;
  }

  bool wet(uint32_t node) const {
    return (shape[node] & wetShape) != 0;
  }

  // The open space a player at a node has: from a floor to its ceiling, or from a wet node's bottom to its interval's ceiling (a
  // swimmer's head may stand above the water).
  int space(uint32_t node) const {
    return static_cast<int>(wet(node) ? intervalHigh[node] : high[node]) - static_cast<int>(low[node]);
  }
};

Grid planGrid(const ReachInput& input) {
  float low[3] = {std::numeric_limits<float>::max(), std::numeric_limits<float>::max(), std::numeric_limits<float>::max()};
  float high[3] = {-std::numeric_limits<float>::max(), -std::numeric_limits<float>::max(), -std::numeric_limits<float>::max()};
  auto take = [&](const float* point) {
    for (int axis = 0; axis < 3; ++axis) {
      low[axis] = std::min(low[axis], point[axis]);
      high[axis] = std::max(high[axis], point[axis]);
    }
  };
  for (size_t corner = 0; corner < input.vertices.size() / 3; ++corner) {
    take(&input.vertices[corner * 3]);
  }
  for (const Box& box : input.swimBoxes) {
    take(box.low);
    take(box.high);
  }
  for (const ZoneLine& line : input.zoneLines) {
    take(line.box.low);
    take(line.box.high);
  }
  Grid grid;
  grid.cellSize = input.cellSize;
  grid.cellHeight = input.cellHeight;
  grid.tileCells = input.tileCells;
  // An empty column all round, so stepping off the zone's edge is a fall onto nothing; and a cell under everything, so height 0 is free
  // to mean "no floor".
  grid.origin[0] = (std::floor(low[0] / grid.cellSize) - 1.0f) * grid.cellSize;
  grid.origin[2] = (std::floor(low[2] / grid.cellSize) - 1.0f) * grid.cellSize;
  grid.origin[1] = (std::floor(low[1] / grid.cellHeight) - 1.0f) * grid.cellHeight;
  const long long columnsX = static_cast<long long>(std::floor((high[0] - grid.origin[0]) / grid.cellSize)) + 2;
  const long long columnsZ = static_cast<long long>(std::floor((high[2] - grid.origin[2]) / grid.cellSize)) + 2;
  grid.tilesX = static_cast<int>((columnsX + grid.tileCells - 1) / grid.tileCells);
  grid.tilesZ = static_cast<int>((columnsZ + grid.tileCells - 1) / grid.tileCells);
  grid.columns = static_cast<long long>(grid.tilesX) * grid.tilesZ * grid.tileCells * grid.tileCells;
  const double verticalCells = std::ceil((high[1] - grid.origin[1]) / grid.cellHeight) + 1.0;
  if (verticalCells > highestCell) {
    throw HelperError("the reach grid is " + std::to_string(static_cast<long long>(verticalCells)) + " cells of " + jsonFloat(grid.cellHeight) +
      " tall, more than the lane's " + std::to_string(highestCell) + "; the zone spans " + jsonFloat(high[1] - low[1]) + " in height");
  }
  if (grid.columns > mostColumns) {
    throw HelperError("the reach grid needs " + std::to_string(grid.tilesX) + " x " + std::to_string(grid.tilesZ) + " tiles of " +
      std::to_string(grid.tileCells) + " x " + std::to_string(grid.tileCells) + " columns of " + jsonFloat(grid.cellSize) + " = " +
      std::to_string(grid.columns) + " columns, more than the helper's " + std::to_string(mostColumns));
  }
  grid.verticalCells = static_cast<int>(verticalCells);
  grid.slabs = (grid.verticalCells + slabCells - 1) / slabCells;
  return grid;
}

void mergeSpan(std::vector<Span>& merged, const Span& span, int flagMerge) {
  if (!merged.empty() && span.low <= merged.back().high) {
    Span& last = merged.back();
    if (std::abs(span.high - last.high) <= flagMerge) {
      last.area = std::max(last.area, span.area);
    } else if (span.high > last.high) {
      last.area = span.area;
    }
    last.high = std::max(last.high, span.high);
    return;
  }
  merged.push_back(span);
}

TileNodes buildTile(const Model& model, const rcChunkyTriMesh& chunks, const std::vector<unsigned char>& chunkAreas, const std::vector<int>& chunkWalkable,
    std::vector<int>& chunkIDs, int tileX, int tileZ) {
  const ReachInput& input = model.input;
  const Grid& grid = model.grid;
  const int tile = grid.tileCells;
  const int side = tile + 2 * tileBorder;
  TileNodes result;
  result.counts.assign(static_cast<size_t>(tile) * tile, 0);
  result.bottomTop.assign(static_cast<size_t>(tile) * tile, openSky);

  float bmin[3] = {grid.origin[0] + (tileX * tile - tileBorder) * grid.cellSize, 0.0f, grid.origin[2] + (tileZ * tile - tileBorder) * grid.cellSize};
  float bmax[3] = {bmin[0] + side * grid.cellSize, 0.0f, bmin[2] + side * grid.cellSize};
  float rectangleMin[2] = {bmin[0], bmin[2]};
  float rectangleMax[2] = {bmax[0], bmax[2]};
  const int chunkCount = rcGetChunksOverlappingRect(&chunks, rectangleMin, rectangleMax, chunkIDs.data(), static_cast<int>(chunkIDs.size()));
  const int vertexCount = static_cast<int>(input.vertices.size() / 3);
  QuietContext context;
  std::vector<rcHeightfield*> solids;
  auto release = [&]() {
    for (rcHeightfield* solid : solids) {
      rcFreeHeightField(solid);
    }
  };
  for (int slab = 0; slab < grid.slabs && chunkCount > 0; ++slab) {
    bmin[1] = grid.origin[1] + static_cast<float>(slab) * slabCells * grid.cellHeight;
    bmax[1] = bmin[1] + slabCells * grid.cellHeight;
    solids.push_back(rcAllocHeightfield());
    rcHeightfield* solid = solids.back();
    if (!solid || !rcCreateHeightfield(&context, *solid, side, side, bmin, bmax, grid.cellSize, grid.cellHeight)) {
      release();
      result.error = "rcCreateHeightfield failed for reach tile (" + std::to_string(tileX) + ", " + std::to_string(tileZ) + ")";
      return result;
    }
    // Every walkable triangle goes in before any other: a span merged over a lower one keeps the area of whichever is added last when
    // their tops differ by more than the climb, so in this order a merged span is walkable exactly when its top is.
    for (const bool walkable : {true, false}) {
      for (int chunk = 0; chunk < chunkCount; ++chunk) {
        const int nodeIndex = chunkIDs[chunk];
        const rcChunkyTriMeshNode& node = chunks.nodes[nodeIndex];
        const int first = walkable ? node.i : node.i + chunkWalkable[nodeIndex];
        const int count = walkable ? chunkWalkable[nodeIndex] : node.n - chunkWalkable[nodeIndex];
        if (count > 0 && !rcRasterizeTriangles(&context, input.vertices.data(), vertexCount, &chunks.tris[first * 3], &chunkAreas[first], count, *solid, model.climb)) {
          release();
          result.error = "rcRasterizeTriangles failed for reach tile (" + std::to_string(tileX) + ", " + std::to_string(tileZ) + ")" +
            (context.errors.empty() ? "" : ": " + context.errors);
          return result;
        }
      }
    }
    rcFilterLowHangingWalkableObstacles(&context, model.climb, *solid);
  }

  std::vector<int> boxesHere, linesHere;
  for (size_t index = 0; index < model.swimBoxes.size(); ++index) {
    const GridBox& box = model.swimBoxes[index];
    if (box.columnHighX >= tileX * tile && box.columnLowX < (tileX + 1) * tile && box.columnHighZ >= tileZ * tile && box.columnLowZ < (tileZ + 1) * tile) {
      boxesHere.push_back(static_cast<int>(index));
    }
  }
  for (size_t index = 0; index < model.lineBoxes.size(); ++index) {
    const GridBox& box = model.lineBoxes[index];
    if (box.columnHighX >= tileX * tile && box.columnLowX < (tileX + 1) * tile && box.columnHighZ >= tileZ * tile && box.columnLowZ < (tileZ + 1) * tile) {
      linesHere.push_back(static_cast<int>(index));
    }
  }

  auto addNode = [&](int low, int high, int intervalLow, int intervalHigh, int box, uint8_t shape) {
    result.low.push_back(static_cast<uint16_t>(low));
    result.high.push_back(static_cast<uint16_t>(high));
    result.intervalLow.push_back(static_cast<uint16_t>(intervalLow));
    result.intervalHigh.push_back(static_cast<uint16_t>(intervalHigh));
    result.box.push_back(static_cast<int16_t>(box));
    result.shape.push_back(shape);
  };
  std::vector<Span> spans;
  for (int z = 0; z < tile; ++z) {
    for (int x = 0; x < tile; ++x) {
      const size_t local = static_cast<size_t>(z) * tile + x;
      const int columnX = tileX * tile + x;
      const int columnZ = tileZ * tile + z;
      spans.clear();
      for (size_t slab = 0; slab < solids.size(); ++slab) {
        const int offset = static_cast<int>(slab) * slabCells;
        for (const rcSpan* span = solids[slab]->spans[(x + tileBorder) + (z + tileBorder) * side]; span; span = span->next) {
          mergeSpan(spans, {offset + static_cast<int>(span->smin), offset + static_cast<int>(span->smax), static_cast<unsigned char>(span->area)}, model.climb);
        }
      }
      const size_t before = result.low.size();
      if (!spans.empty()) {
        result.bottomTop[local] = static_cast<uint16_t>(spans.front().low);
      }
      for (size_t interval = 0; interval <= spans.size(); ++interval) {
        const int floor = interval == 0 ? noFloor : spans[interval - 1].high;
        const int ceiling = interval == spans.size() ? openSky : spans[interval].low;
        if (floor != noFloor && ceiling - floor >= model.headroomCells) {
          const float point = static_cast<float>(floor + 1);
          int holding = -1;
          for (const int index : boxesHere) {
            const GridBox& box = model.swimBoxes[index];
            if (box.holdsColumn(columnX, columnZ) && point >= box.lowY && point <= box.highY) {
              holding = index;
              break;
            }
          }
          uint8_t shape = spans[interval - 1].area != RC_NULL_AREA ? walkableShape : 0;
          for (const int index : linesHere) {
            const GridBox& line = model.lineBoxes[index];
            if (line.holdsColumn(columnX, columnZ) && point >= line.lowY && point <= line.highY) {
              shape |= absorbedShape;
              break;
            }
          }
          addNode(floor, ceiling, floor, ceiling, holding, shape);
        }
        if (ceiling - floor < model.headroomCells) {
          continue;
        }
        for (const int index : boxesHere) {
          const GridBox& box = model.swimBoxes[index];
          if (!box.holdsColumn(columnX, columnZ)) {
            continue;
          }
          const int wetLow = std::max(floor, static_cast<int>(std::floor(box.lowY)));
          const int wetHigh = std::min(ceiling, static_cast<int>(std::ceil(box.highY)));
          if (wetHigh - wetLow < model.headroomCells) {
            continue;
          }
          uint8_t shape = wetShape;
          for (const int lineIndex : linesHere) {
            const GridBox& line = model.lineBoxes[lineIndex];
            if (!line.holdsColumn(columnX, columnZ) || line.highY < wetLow || line.lowY > wetHigh) {
              continue;
            }
            shape |= line.lowY <= wetLow && line.highY >= wetHigh ? absorbedShape : touchesLineShape;
            if (shape & absorbedShape) {
              break;
            }
          }
          addNode(wetLow, wetHigh, floor, ceiling, index, shape);
        }
      }
      result.counts[local] = static_cast<uint32_t>(result.low.size() - before);
    }
  }
  release();
  return result;
}

void rasterize(Model& model) {
  const ReachInput& input = model.input;
  const Grid& grid = model.grid;
  const int triangleCount = static_cast<int>(input.triangles.size() / 3);
  reportProgress("partitioning collision triangles", 0, 1);
  rcChunkyTriMesh chunks;
  if (!rcCreateChunkyTriMesh(input.vertices.data(), input.triangles.data(), triangleCount, trianglesPerChunk, &chunks)) {
    throw HelperError("rcCreateChunkyTriMesh failed");
  }
  // Both windings count: the client's collision sidedness is untraced, and counting both only adds reach.
  std::vector<unsigned char> chunkAreas(static_cast<size_t>(chunks.ntris));
  for (int index = 0; index < chunks.ntris; ++index) {
    const float* a = &input.vertices[chunks.tris[index * 3 + 0] * 3];
    const float* b = &input.vertices[chunks.tris[index * 3 + 1] * 3];
    const float* c = &input.vertices[chunks.tris[index * 3 + 2] * 3];
    float first[3], second[3], normal[3];
    rcVsub(first, b, a);
    rcVsub(second, c, a);
    rcVcross(normal, first, second);
    const float length = std::sqrt(rcVdot(normal, normal));
    chunkAreas[index] = length > 0.0f && std::fabs(normal[1]) / length >= input.walkableNormalZ ? RC_WALKABLE_AREA : RC_NULL_AREA;
  }
  std::vector<int> chunkWalkable(static_cast<size_t>(chunks.nnodes), 0);
  for (int nodeIndex = 0; nodeIndex < chunks.nnodes; ++nodeIndex) {
    const rcChunkyTriMeshNode& node = chunks.nodes[nodeIndex];
    if (node.i < 0) {
      continue;
    }
    std::vector<int> order(static_cast<size_t>(node.n));
    for (int index = 0; index < node.n; ++index) {
      order[index] = node.i + index;
    }
    std::stable_partition(order.begin(), order.end(), [&](int triangle) { return chunkAreas[triangle] == RC_WALKABLE_AREA; });
    std::vector<int> triangles(static_cast<size_t>(node.n) * 3);
    std::vector<unsigned char> areas(static_cast<size_t>(node.n));
    for (int index = 0; index < node.n; ++index) {
      std::memcpy(&triangles[static_cast<size_t>(index) * 3], &chunks.tris[order[index] * 3], 3 * sizeof(int));
      areas[index] = chunkAreas[order[index]];
      chunkWalkable[nodeIndex] += areas[index] == RC_WALKABLE_AREA ? 1 : 0;
    }
    std::memcpy(&chunks.tris[node.i * 3], triangles.data(), triangles.size() * sizeof(int));
    std::memcpy(&chunkAreas[node.i], areas.data(), areas.size());
  }

  const long long tileCount = static_cast<long long>(grid.tilesX) * grid.tilesZ;
  std::vector<TileNodes> results(static_cast<size_t>(tileCount));
  std::atomic<long long> nextTile(0);
  std::atomic<long long> builtCount(0);
  std::mutex progressLock;
  long long reportedPercent = -1;
  auto work = [&]() {
    std::vector<int> chunkIDs(static_cast<size_t>(chunks.nnodes));
    while (true) {
      const long long tile = nextTile++;
      if (tile >= tileCount) {
        return;
      }
      results[static_cast<size_t>(tile)] = buildTile(model, chunks, chunkAreas, chunkWalkable, chunkIDs, static_cast<int>(tile % grid.tilesX), static_cast<int>(tile / grid.tilesX));
      const long long done = ++builtCount;
      std::lock_guard<std::mutex> guard(progressLock);
      const long long percent = done * 100 / tileCount;
      if (percent > reportedPercent) {
        reportedPercent = percent;
        reportProgress("rasterizing reach tiles", done, tileCount);
      }
    }
  };
  const unsigned int threadCount = static_cast<unsigned int>(std::min<long long>(input.threads, tileCount));
  std::vector<std::thread> workers;
  for (unsigned int index = 0; index < threadCount; ++index) {
    workers.emplace_back(work);
  }
  for (std::thread& worker : workers) {
    worker.join();
  }
  size_t total = 0;
  for (const TileNodes& result : results) {
    if (!result.error.empty()) {
      throw HelperError(result.error);
    }
    total += result.low.size();
  }
  if (total >= std::numeric_limits<uint32_t>::max()) {
    throw HelperError("the reach grid holds " + std::to_string(total) + " nodes, more than the lane addresses");
  }
  const size_t perTile = static_cast<size_t>(grid.tileCells) * grid.tileCells;
  model.columnStart.resize(static_cast<size_t>(grid.columns) + 1);
  model.bottomTop.resize(static_cast<size_t>(grid.columns));
  model.low.reserve(total);
  model.high.reserve(total);
  model.intervalLow.reserve(total);
  model.intervalHigh.reserve(total);
  model.box.reserve(total);
  model.shape.reserve(total);
  uint32_t start = 0;
  for (long long tile = 0; tile < tileCount; ++tile) {
    TileNodes& result = results[static_cast<size_t>(tile)];
    for (size_t local = 0; local < perTile; ++local) {
      model.columnStart[static_cast<size_t>(tile) * perTile + local] = start;
      model.bottomTop[static_cast<size_t>(tile) * perTile + local] = result.bottomTop[local];
      start += result.counts[local];
    }
    model.low.insert(model.low.end(), result.low.begin(), result.low.end());
    model.high.insert(model.high.end(), result.high.begin(), result.high.end());
    model.intervalLow.insert(model.intervalLow.end(), result.intervalLow.begin(), result.intervalLow.end());
    model.intervalHigh.insert(model.intervalHigh.end(), result.intervalHigh.begin(), result.intervalHigh.end());
    model.box.insert(model.box.end(), result.box.begin(), result.box.end());
    model.shape.insert(model.shape.end(), result.shape.begin(), result.shape.end());
    result = TileNodes();
  }
  model.columnStart[static_cast<size_t>(grid.columns)] = start;
}

struct Event {
  uint8_t kind;
  uint32_t from;
  uint32_t column;
  int height;
  int line;
};

struct Queued {
  uint32_t node;
  uint32_t column;
};

// Runs a task over numbered chunks on the helper's threads, the caller's among them, and returns when every chunk is done.
class WorkerPool {
public:
  explicit WorkerPool(unsigned int count) {
    for (unsigned int index = 1; index < count; ++index) {
      workers.emplace_back([this]() { serve(); });
    }
  }

  ~WorkerPool() {
    {
      std::lock_guard<std::mutex> guard(lock);
      stopping = true;
      ++generation;
    }
    wake.notify_all();
    for (std::thread& worker : workers) {
      worker.join();
    }
  }

  size_t size() const {
    return workers.size() + 1;
  }

  void run(size_t chunkCount, const std::function<void(size_t)>& work) {
    if (chunkCount <= 1 || workers.empty()) {
      for (size_t chunk = 0; chunk < chunkCount; ++chunk) {
        work(chunk);
      }
      return;
    }
    {
      std::lock_guard<std::mutex> guard(lock);
      task = &work;
      chunks = chunkCount;
      next = 0;
      busy = workers.size();
      failure.clear();
      ++generation;
    }
    wake.notify_all();
    drain();
    std::unique_lock<std::mutex> guard(lock);
    finished.wait(guard, [&]() { return busy == 0; });
    task = nullptr;
    if (!failure.empty()) {
      throw HelperError(failure);
    }
  }

private:
  void drain() {
    for (size_t chunk = next++; chunk < chunks; chunk = next++) {
      try {
        (*task)(chunk);
      } catch (const std::bad_alloc&) {
        fail("out of memory");
      } catch (const std::exception& error) {
        fail(error.what());
      }
    }
  }

  void fail(const std::string& message) {
    std::lock_guard<std::mutex> guard(lock);
    if (failure.empty()) {
      failure = message;
    }
  }

  void serve() {
    size_t seen = 0;
    while (true) {
      {
        std::unique_lock<std::mutex> guard(lock);
        wake.wait(guard, [&]() { return generation != seen; });
        seen = generation;
        if (stopping) {
          return;
        }
      }
      drain();
      {
        std::lock_guard<std::mutex> guard(lock);
        --busy;
      }
      finished.notify_one();
    }
  }

  std::vector<std::thread> workers;
  std::mutex lock;
  std::condition_variable wake, finished;
  const std::function<void(size_t)>* task = nullptr;
  size_t chunks = 0;
  std::atomic<size_t> next{0};
  size_t busy = 0;
  size_t generation = 0;
  bool stopping = false;
  std::string failure;
};

// The lane's floods: every move of the rules in docs/zoneWorkflow.md (Player space: reach), evaluated on demand from the nodes, a level
// at a time on the pool. Reached sets never depend on the threads; where a node's predecessor is kept, the earliest node of the level
// before that moves into it is taken, so the lane is the same run to run.
class Flooder {
public:
  Flooder(Model& source, WorkerPool& workers) : model(source), grid(source.grid), pool(workers), count(source.nodeCount()) {
    state.reset(new std::atomic<uint8_t>[count]);
    claim.reset(new std::atomic<uint32_t>[count]);
    for (uint32_t node = 0; node < count; ++node) {
      state[node].store(0, std::memory_order_relaxed);
    }
    originSeed.assign(count, noSeed);
    predecessor.assign(count, -1);
    move.assign(count, unreachedMove);
    lineReached.reset(new std::atomic<uint8_t>[model.lineBoxes.size() + 1]);
    for (size_t line = 0; line < model.lineBoxes.size(); ++line) {
      lineReached[line].store(0, std::memory_order_relaxed);
    }
    landing.assign(model.lineBoxes.size(), -1);
  }

  Model& model;
  const Grid& grid;
  WorkerPool& pool;
  const uint32_t count;
  std::unique_ptr<std::atomic<uint8_t>[]> state;
  std::unique_ptr<std::atomic<uint32_t>[]> claim;
  std::vector<uint16_t> originSeed;
  std::vector<int32_t> predecessor;
  std::vector<uint8_t> move;
  std::unique_ptr<std::atomic<uint8_t>[]> lineReached;
  std::vector<int64_t> landing;
  std::vector<uint32_t> landingColumn;
  std::vector<Event> events;
  std::vector<std::pair<uint32_t, int>> nodeLines;

  bool has(uint32_t node, uint8_t bits) const {
    return (state[node].load(std::memory_order_relaxed) & bits) != 0;
  }

  void set(uint32_t node, uint8_t bits) {
    state[node].fetch_or(bits, std::memory_order_relaxed);
  }

  int lineOfNode(uint32_t node) const {
    auto found = std::lower_bound(nodeLines.begin(), nodeLines.end(), std::make_pair(node, -1));
    return found != nodeLines.end() && found->first == node ? found->second : -1;
  }

  void indexNodeLines() {
    for (long long column = 0; column < grid.columns; ++column) {
      const uint32_t first = model.columnStart[static_cast<size_t>(column)];
      const uint32_t last = model.columnStart[static_cast<size_t>(column) + 1];
      if (first == last) {
        continue;
      }
      int x, z;
      grid.columnPlace(column, x, z);
      for (uint32_t node = first; node < last; ++node) {
        if (!(model.shape[node] & (absorbedShape | touchesLineShape))) {
          continue;
        }
        for (size_t index = 0; index < model.lineBoxes.size(); ++index) {
          const GridBox& line = model.lineBoxes[index];
          if (!line.holdsColumn(x, z)) {
            continue;
          }
          const bool holds = model.wet(node)
            ? line.highY >= model.low[node] && line.lowY <= model.high[node]
            : static_cast<float>(model.low[node] + 1) >= line.lowY && static_cast<float>(model.low[node] + 1) <= line.highY;
          if (holds) {
            nodeLines.emplace_back(node, static_cast<int>(index));
            break;
          }
        }
      }
    }
  }

  // The floor node under a point, found as an entry's footing: the highest floor from footingAbove over it down to footingBelow under it.
  int64_t footingNode(const float* point, long long& column) const {
    const int x = static_cast<int>(std::floor((point[0] - grid.origin[0]) / grid.cellSize));
    const int z = static_cast<int>(std::floor((point[2] - grid.origin[2]) / grid.cellSize));
    if (x < 0 || z < 0 || x >= grid.columnsX() || z >= grid.columnsZ()) {
      return -1;
    }
    column = grid.columnOf(x, z);
    const float top = grid.cellsAt(point[1] + model.input.footingAbove);
    const float bottom = grid.cellsAt(point[1] - model.input.footingBelow);
    int64_t found = -1;
    for (uint32_t node = model.columnStart[static_cast<size_t>(column)]; node < model.columnStart[static_cast<size_t>(column) + 1]; ++node) {
      if (!model.wet(node) && model.low[node] >= bottom && model.low[node] <= top) {
        found = node;
      }
    }
    return found;
  }

  bool neighbour(long long column, int direction, long long& next) const {
    int x, z;
    grid.columnPlace(column, x, z);
    static const int steps[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
    x += steps[direction][0];
    z += steps[direction][1];
    if (x < 0 || z < 0 || x >= grid.columnsX() || z >= grid.columnsZ()) {
      return false;
    }
    next = grid.columnOf(x, z);
    return true;
  }

  // A fall in a column from height start through one of its open intervals, down to its floor node (or nothing when the interval is
  // the bottom one): it lands in the first swim box or zone line it meets on the way, else on the floor; under the underworld or onto
  // nothing it is an event.
  template <class Visit, class Record>
  void fall(uint32_t from, long long column, int start, int intervalLow, int intervalHigh, int64_t floorNode, int64_t self, uint8_t kind, int headroom,
      Visit& visit, Record& record) const {
    int64_t wetLanding = -1;
    int wetHeight = -1;
    for (uint32_t node = model.columnStart[static_cast<size_t>(column)]; node < model.columnStart[static_cast<size_t>(column) + 1]; ++node) {
      if (!model.wet(node) || node == self || model.intervalLow[node] != intervalLow || model.intervalHigh[node] != intervalHigh || model.low[node] > start) {
        continue;
      }
      const int height = std::min<int>(model.high[node], start);
      if (height > wetHeight) {
        wetHeight = height;
        wetLanding = node;
      }
    }
    int x, z;
    grid.columnPlace(column, x, z);
    int lineLanding = -1;
    float lineHeight = -1.0f;
    for (size_t index = 0; index < model.lineBoxes.size(); ++index) {
      const GridBox& line = model.lineBoxes[index];
      if (!line.holdsColumn(x, z) || line.lowY > start || line.highY < intervalLow) {
        continue;
      }
      const float height = std::min(line.highY, static_cast<float>(start));
      if (height > lineHeight) {
        lineHeight = height;
        lineLanding = static_cast<int>(index);
      }
    }
    if (lineLanding >= 0 && lineHeight > wetHeight) {
      record(absorbedEvent, from, column, static_cast<int>(std::floor(lineHeight)), lineLanding);
      if (landing[lineLanding] >= 0) {
        visit(static_cast<uint32_t>(landing[lineLanding]), static_cast<uint32_t>(landingColumn[lineLanding]), teleportMove);
      }
      return;
    }
    if (wetLanding >= 0) {
      if (model.space(static_cast<uint32_t>(wetLanding)) >= headroom) {
        visit(static_cast<uint32_t>(wetLanding), static_cast<uint32_t>(column), kind);
      }
      return;
    }
    if (floorNode < 0) {
      record(voidEvent, from, column, start, -1);
      return;
    }
    if (model.input.hasUnderworld && static_cast<float>(model.low[floorNode]) < model.underworldCells) {
      record(belowUnderworldEvent, from, column, model.low[floorNode], -1);
      return;
    }
    if (model.space(static_cast<uint32_t>(floorNode)) >= headroom) {
      visit(static_cast<uint32_t>(floorNode), static_cast<uint32_t>(column), kind);
    }
  }

  // Every move out of a node at a headroom, into one column only when only >= 0.
  template <class Visit, class Record>
  void successors(uint32_t node, long long column, int headroom, long long only, Visit& visit, Record& record) const {
    if (model.shape[node] & absorbedShape) {
      const int line = lineOfNode(node);
      if (line >= 0 && landing[line] >= 0) {
        visit(static_cast<uint32_t>(landing[line]), landingColumn[line], teleportMove);
      }
      return;
    }
    const uint32_t first = model.columnStart[static_cast<size_t>(column)];
    const uint32_t last = model.columnStart[static_cast<size_t>(column) + 1];
    const int low = model.low[node];
    const int high = model.high[node];
    if (!model.wet(node)) {
      const bool walkable = model.walkable(node);
      if (only < 0 || only == column) {
        for (uint32_t other = first; other < last; ++other) {
          if (model.wet(other) && model.intervalLow[other] == low && model.intervalHigh[other] == high && model.low[other] - low <= model.playerCells &&
              model.space(other) >= headroom) {
            visit(other, static_cast<uint32_t>(column), enterWaterMove);
          }
        }
      }
      for (int direction = 0; direction < 4; ++direction) {
        long long next;
        if (!neighbour(column, direction, next) || (only >= 0 && only != next)) {
          continue;
        }
        for (uint32_t other = model.columnStart[static_cast<size_t>(next)]; other < model.columnStart[static_cast<size_t>(next) + 1]; ++other) {
          if (model.wet(other)) {
            continue;
          }
          const int otherLow = model.low[other];
          const int overlap = std::min<int>(high, model.high[other]) - std::max(low, otherLow);
          if (overlap < headroom || model.space(other) < headroom) {
            continue;
          }
          const bool otherWalkable = model.walkable(other);
          if (otherLow > low) {
            if (walkable && otherLow - low <= model.climb) {
              visit(other, static_cast<uint32_t>(next), otherWalkable ? walkMove : slideMove);
            }
          } else if (low - otherLow <= model.climb) {
            // Off a face too steep to stand on, a player goes level or down; level matters on the rim a vertical face leaves along a
            // ledge's top, whose columns stand at the ledge's height without a walkable face.
            visit(other, static_cast<uint32_t>(next), walkable && otherWalkable ? walkMove : slideMove);
          } else {
            fall(node, next, low, otherLow, model.high[other], other, -1, dropMove, headroom, visit, record);
          }
        }
        if (model.bottomTop[static_cast<size_t>(next)] >= low + headroom) {
          fall(node, next, low, noFloor, model.bottomTop[static_cast<size_t>(next)], -1, -1, dropMove, headroom, visit, record);
        }
      }
      return;
    }

    const int boxIndex = model.box[node];
    const int ceiling = model.intervalHigh[node];
    if (only < 0 || only == column) {
      int64_t floorNode = -1;
      for (uint32_t other = first; other < last; ++other) {
        if (!model.wet(other) && model.low[other] == model.intervalLow[node] && model.high[other] == ceiling) {
          floorNode = other;
        }
      }
      fall(node, column, low, model.intervalLow[node], ceiling, floorNode, node, sinkMove, headroom, visit, record);
    }
    for (int direction = 0; direction < 4; ++direction) {
      long long next;
      if (!neighbour(column, direction, next) || (only >= 0 && only != next)) {
        continue;
      }
      const uint32_t nextFirst = model.columnStart[static_cast<size_t>(next)];
      const uint32_t nextLast = model.columnStart[static_cast<size_t>(next) + 1];
      for (uint32_t other = nextFirst; other < nextLast; ++other) {
        if (!model.wet(other)) {
          continue;
        }
        const int otherBox = model.box[other];
        if (otherBox != boxIndex && !model.touching[boxIndex][otherBox]) {
          continue;
        }
        const int water = std::min<int>(high, model.high[other]) - std::max<int>(low, model.low[other]);
        const int open = std::min<int>(ceiling, model.intervalHigh[other]) - std::max<int>(low, model.low[other]);
        if (water > 0 && open >= headroom && model.space(other) >= headroom) {
          visit(other, static_cast<uint32_t>(next), swimMove);
        }
      }
      for (uint32_t other = nextFirst; other < nextLast; ++other) {
        if (model.wet(other)) {
          continue;
        }
        const int otherLow = model.low[other];
        if (otherLow >= low) {
          if (model.walkable(other) && otherLow <= model.swimTops[boxIndex] + model.climb && ceiling >= otherLow + headroom && model.space(other) >= headroom) {
            visit(other, static_cast<uint32_t>(next), climbOutMove);
          }
        } else if (model.high[other] >= low + headroom && ceiling >= low + headroom) {
          fall(node, next, low, otherLow, model.high[other], other, -1, dropMove, headroom, visit, record);
        }
      }
      if (model.bottomTop[static_cast<size_t>(next)] >= low + headroom && ceiling >= low + headroom) {
        fall(node, next, low, noFloor, model.bottomTop[static_cast<size_t>(next)], -1, -1, dropMove, headroom, visit, record);
      }
    }
  }

  size_t chunksFor(size_t entries) const {
    return entries < parallelLevel ? 1 : std::min(pool.size() * 8, entries / (parallelLevel / 8));
  }

  void forEachNode(const std::function<void(uint32_t)>& work) {
    const size_t chunkCount = std::max<size_t>(1, std::min<size_t>(pool.size() * 4, count / 65536));
    const size_t chunkSize = (count + chunkCount - 1) / chunkCount;
    pool.run(chunkCount, [&](size_t chunk) {
      const size_t last = std::min<size_t>(count, (chunk + 1) * chunkSize);
      for (size_t node = chunk * chunkSize; node < last; ++node) {
        work(static_cast<uint32_t>(node));
      }
    });
  }

  // A forward flood at a headroom from the queued nodes, already marked, marking each node it reaches. A node it reaches that is no seed
  // takes its predecessor, move, and origin seed from it, unless only nodes the full flood missed take them (assignAll false); and the
  // void, underworld, and zone line events met on the way are kept when recordEvents.
  void forwardFlood(std::vector<Queued> frontier, uint8_t mark, int headroom, bool assignAll, bool recordEvents) {
    forEachNode([&](uint32_t node) { claim[node].store(unclaimed, std::memory_order_relaxed); });
    auto quiet = [](uint8_t, uint32_t, long long, int, int) {};
    while (!frontier.empty()) {
      const size_t chunkCount = chunksFor(frontier.size());
      const size_t chunkSize = (frontier.size() + chunkCount - 1) / chunkCount;
      // On one thread the first node of the level to move into a node is the one that claims it, so claims are taken as they come.
      const bool claimInOrder = chunkCount == 1;
      if (!claimInOrder) {
        pool.run(chunkCount, [&](size_t chunk) {
          const size_t last = std::min(frontier.size(), (chunk + 1) * chunkSize);
          for (size_t index = chunk * chunkSize; index < last; ++index) {
            const uint32_t claimer = static_cast<uint32_t>(index);
            auto visit = [&](uint32_t target, uint32_t, uint8_t) {
              if (has(target, mark)) {
                return;
              }
              uint32_t held = claim[target].load(std::memory_order_relaxed);
              while (claimer < held && !claim[target].compare_exchange_weak(held, claimer, std::memory_order_relaxed)) {
              }
            };
            successors(frontier[index].node, frontier[index].column, headroom, -1, visit, quiet);
          }
        });
      }
      std::vector<std::vector<Queued>> nextParts(chunkCount);
      std::vector<std::vector<Event>> eventParts(chunkCount);
      pool.run(chunkCount, [&](size_t chunk) {
        const size_t last = std::min(frontier.size(), (chunk + 1) * chunkSize);
        for (size_t index = chunk * chunkSize; index < last; ++index) {
          const Queued entry = frontier[index];
          const uint32_t claimer = static_cast<uint32_t>(index);
          if (model.shape[entry.node] & (absorbedShape | touchesLineShape)) {
            const int line = lineOfNode(entry.node);
            if (line >= 0) {
              lineReached[line].store(1, std::memory_order_relaxed);
            }
          }
          auto visit = [&](uint32_t target, uint32_t column, uint8_t kind) {
            if (has(target, mark) || (!claimInOrder && claim[target].load(std::memory_order_relaxed) != claimer)) {
              return;
            }
            set(target, mark);
            if (!has(target, seedState) && (assignAll || !has(target, reachedFullState))) {
              predecessor[target] = static_cast<int32_t>(entry.node);
              move[target] = kind;
              originSeed[target] = originSeed[entry.node];
            }
            nextParts[chunk].push_back({target, column});
          };
          auto record = [&](uint8_t kind, uint32_t from, long long column, int height, int line) {
            if (line >= 0) {
              lineReached[line].store(1, std::memory_order_relaxed);
            }
            if (recordEvents) {
              eventParts[chunk].push_back({kind, from, static_cast<uint32_t>(column), height, line});
            }
          };
          successors(entry.node, entry.column, headroom, -1, visit, record);
        }
      });
      frontier.clear();
      for (size_t chunk = 0; chunk < chunkCount; ++chunk) {
        frontier.insert(frontier.end(), nextParts[chunk].begin(), nextParts[chunk].end());
        events.insert(events.end(), eventParts[chunk].begin(), eventParts[chunk].end());
      }
    }
  }

  // A reverse flood at a headroom over the reached nodes, from the queued nodes, already marked, back along every move into them;
  // teleports are (landing, source) pairs sorted by landing.
  void reverseFlood(std::vector<Queued> frontier, uint8_t mark, int headroom, const std::vector<std::pair<uint32_t, uint32_t>>& teleports) {
    auto quiet = [](uint8_t, uint32_t, long long, int, int) {};
    while (!frontier.empty()) {
      const size_t chunkCount = chunksFor(frontier.size());
      const size_t chunkSize = (frontier.size() + chunkCount - 1) / chunkCount;
      std::vector<std::vector<Queued>> nextParts(chunkCount);
      pool.run(chunkCount, [&](size_t chunk) {
        const size_t last = std::min(frontier.size(), (chunk + 1) * chunkSize);
        for (size_t index = chunk * chunkSize; index < last; ++index) {
          const Queued entry = frontier[index];
          auto consider = [&](uint32_t candidate, long long candidateColumn) {
            if (!has(candidate, reachedState) || has(candidate, mark)) {
              return;
            }
            bool found = false;
            auto check = [&](uint32_t reached, uint32_t, uint8_t) {
              found = found || reached == entry.node;
            };
            successors(candidate, candidateColumn, headroom, static_cast<long long>(entry.column), check, quiet);
            if (found && !(state[candidate].fetch_or(mark, std::memory_order_relaxed) & mark)) {
              nextParts[chunk].push_back({candidate, static_cast<uint32_t>(candidateColumn)});
            }
          };
          const long long column = entry.column;
          for (uint32_t candidate = model.columnStart[static_cast<size_t>(column)]; candidate < model.columnStart[static_cast<size_t>(column) + 1]; ++candidate) {
            consider(candidate, column);
          }
          for (int direction = 0; direction < 4; ++direction) {
            long long next;
            if (!neighbour(column, direction, next)) {
              continue;
            }
            for (uint32_t candidate = model.columnStart[static_cast<size_t>(next)]; candidate < model.columnStart[static_cast<size_t>(next) + 1]; ++candidate) {
              consider(candidate, next);
            }
          }
          auto teleport = std::lower_bound(teleports.begin(), teleports.end(), std::make_pair(entry.node, 0u));
          for (; teleport != teleports.end() && teleport->first == entry.node; ++teleport) {
            if (has(teleport->second, reachedState) && !(state[teleport->second].fetch_or(mark, std::memory_order_relaxed) & mark)) {
              nextParts[chunk].push_back({teleport->second, columnOfNode(teleport->second)});
            }
          }
        }
      });
      frontier.clear();
      for (const std::vector<Queued>& part : nextParts) {
        frontier.insert(frontier.end(), part.begin(), part.end());
      }
    }
  }

  uint32_t columnOfNode(uint32_t node) const {
    const auto found = std::upper_bound(model.columnStart.begin(), model.columnStart.end(), node);
    return static_cast<uint32_t>(found - model.columnStart.begin() - 1);
  }
};

template <class Value>
void writeArray(std::ofstream& output, const Value* values, size_t count) {
  output.write(reinterpret_cast<const char*>(values), static_cast<std::streamsize>(count * sizeof(Value)));
}

void writeLane(const std::string& path, const Model& model, const Flooder& flooder, const std::vector<int64_t>& seedNodes) {
  const Grid& grid = model.grid;
  const uint32_t count = model.nodeCount();
  std::ofstream output(path, std::ios::binary);
  if (!output) {
    throw HelperError("cannot write " + path);
  }
  output.write(laneMagic, 8);
  writeArray(output, grid.origin, 3);
  writeArray(output, &grid.cellSize, 1);
  writeArray(output, &grid.cellHeight, 1);
  const int32_t sizes[4] = {grid.tileCells, grid.tilesX, grid.tilesZ, grid.verticalCells};
  writeArray(output, sizes, 4);
  writeArray(output, &count, 1);
  writeArray(output, model.columnStart.data(), model.columnStart.size());
  writeArray(output, model.low.data(), count);
  writeArray(output, model.high.data(), count);
  writeArray(output, model.box.data(), count);
  writeArray(output, model.shape.data(), count);
  std::vector<uint8_t> states(count);
  for (uint32_t node = 0; node < count; ++node) {
    states[node] = static_cast<uint8_t>(flooder.state[node].load(std::memory_order_relaxed) & laneStates);
  }
  writeArray(output, states.data(), count);
  writeArray(output, flooder.originSeed.data(), count);
  writeArray(output, flooder.predecessor.data(), count);
  writeArray(output, flooder.move.data(), count);
  const uint32_t eventCount = static_cast<uint32_t>(flooder.events.size());
  writeArray(output, &eventCount, 1);
  for (const Event& event : flooder.events) {
    const int32_t values[5] = {event.kind, static_cast<int32_t>(event.from), static_cast<int32_t>(event.column), event.height, event.line};
    writeArray(output, values, 5);
  }
  const uint32_t lineCount = static_cast<uint32_t>(model.lineBoxes.size());
  writeArray(output, &lineCount, 1);
  for (uint32_t line = 0; line < lineCount; ++line) {
    const int32_t values[2] = {flooder.lineReached[line].load(std::memory_order_relaxed), static_cast<int32_t>(flooder.landing[line])};
    writeArray(output, values, 2);
  }
  const uint32_t seedCount = static_cast<uint32_t>(seedNodes.size());
  writeArray(output, &seedCount, 1);
  for (const int64_t node : seedNodes) {
    const int32_t value = static_cast<int32_t>(node);
    writeArray(output, &value, 1);
  }
  output.close();
  if (!output) {
    throw HelperError("cannot write " + path);
  }
}

double secondsSince(std::chrono::steady_clock::time_point start) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

}

void runReach(const std::string& inputPath, const std::string& lanePath, const std::string& reportPath) {
  const auto start = std::chrono::steady_clock::now();
  const ReachInput input = readReachInput(inputPath);
  Model model(input);
  model.grid = planGrid(input);
  const Grid& grid = model.grid;
  model.climb = static_cast<int>(std::floor(input.stepHeight / input.cellHeight));
  model.playerCells = static_cast<int>(std::ceil(input.playerHeight / input.cellHeight));
  model.headroomCells = std::max(1, static_cast<int>(std::ceil(input.containmentHeadroom / input.cellHeight - 1e-4f)));
  model.underworldCells = grid.cellsAt(input.underworld);
  for (const Box& box : input.swimBoxes) {
    model.swimBoxes.push_back(gridBox(grid, box));
    model.swimTops.push_back(static_cast<int>(std::ceil(model.swimBoxes.back().highY)));
  }
  for (const ZoneLine& line : input.zoneLines) {
    model.lineBoxes.push_back(gridBox(grid, line.box));
  }
  const float touchingGap = 1e-3f;
  model.touching.assign(input.swimBoxes.size(), std::vector<uint8_t>(input.swimBoxes.size(), 0));
  for (size_t first = 0; first < input.swimBoxes.size(); ++first) {
    for (size_t second = 0; second < input.swimBoxes.size(); ++second) {
      bool touches = true;
      for (int axis = 0; axis < 3; ++axis) {
        touches = touches && input.swimBoxes[first].low[axis] <= input.swimBoxes[second].high[axis] + touchingGap &&
          input.swimBoxes[second].low[axis] <= input.swimBoxes[first].high[axis] + touchingGap;
      }
      model.touching[first][second] = touches ? 1 : 0;
    }
  }

  rasterize(model);
  const double rasterSeconds = secondsSince(start);
  WorkerPool pool(input.threads);
  Flooder flooder(model, pool);
  flooder.indexNodeLines();

  std::vector<std::string> notFollowed(input.zoneLines.size());
  flooder.landingColumn.assign(input.zoneLines.size(), 0);
  for (size_t line = 0; line < input.zoneLines.size(); ++line) {
    if (!input.zoneLines[line].teleports) {
      continue;
    }
    long long column = 0;
    flooder.landing[line] = flooder.footingNode(input.zoneLines[line].target, column);
    flooder.landingColumn[line] = static_cast<uint32_t>(column);
    if (flooder.landing[line] < 0) {
      notFollowed[line] = "no floor lies from " + jsonFloat(input.footingAbove) + " over its target down to " + jsonFloat(input.footingBelow) + " under it";
    }
  }

  std::vector<int64_t> seedNodes;
  std::vector<Queued> seeds;
  for (size_t index = 0; index < input.seeds.size(); ++index) {
    const Seed& seed = input.seeds[index];
    const int x = static_cast<int>(std::floor((seed.point[0] - grid.origin[0]) / grid.cellSize));
    const int z = static_cast<int>(std::floor((seed.point[2] - grid.origin[2]) / grid.cellSize));
    if (x < 0 || z < 0 || x >= grid.columnsX() || z >= grid.columnsZ()) {
      throw HelperError("seed '" + seed.name + "' at " + zoneAxesText(seed.point) + " lies outside the reach grid");
    }
    const long long column = grid.columnOf(x, z);
    const float height = grid.cellsAt(seed.point[1]);
    int64_t best = -1;
    float bestDistance = seed.tolerance / input.cellHeight;
    float nearest = std::numeric_limits<float>::max();
    for (uint32_t node = model.columnStart[static_cast<size_t>(column)]; node < model.columnStart[static_cast<size_t>(column) + 1]; ++node) {
      if (model.wet(node)) {
        continue;
      }
      const float distance = std::fabs(static_cast<float>(model.low[node]) - height);
      nearest = std::min(nearest, distance);
      if (distance <= bestDistance) {
        bestDistance = distance;
        best = node;
      }
    }
    if (best < 0) {
      throw HelperError("seed '" + seed.name + "' at " + zoneAxesText(seed.point) + " has no lane node within " + jsonFloat(seed.tolerance) +
        " of its footing" + (nearest < std::numeric_limits<float>::max() ? "; the nearest floor in its column lies " + jsonFloat(nearest * input.cellHeight) + " away" : "; its column holds no floor"));
    }
    seedNodes.push_back(best);
    if (!flooder.has(static_cast<uint32_t>(best), seedState)) {
      flooder.set(static_cast<uint32_t>(best), seedState);
      flooder.originSeed[best] = static_cast<uint16_t>(index);
      flooder.move[best] = seedMove;
      seeds.push_back({static_cast<uint32_t>(best), static_cast<uint32_t>(column)});
    }
  }

  const auto floodStart = std::chrono::steady_clock::now();
  reportProgress("flooding the lane", 0, 4);
  std::vector<Queued> frontier;
  for (const Queued& seed : seeds) {
    if (model.space(seed.node) >= model.playerCells) {
      flooder.set(seed.node, reachedFullState);
      frontier.push_back(seed);
    }
  }
  flooder.forwardFlood(frontier, reachedFullState, model.playerCells, true, false);
  const double fullSeconds = secondsSince(floodStart);
  reportProgress("flooding the lane", 1, 4);

  const auto containmentStart = std::chrono::steady_clock::now();
  std::vector<Queued> first, rest;
  for (size_t index = 0; index < seeds.size(); ++index) {
    (input.safeSeed >= 0 && seeds[index].node == seedNodes[input.safeSeed] ? first : rest).push_back(seeds[index]);
  }
  for (const std::vector<Queued>* phase : {&first, &rest}) {
    std::vector<Queued> phaseFrontier;
    for (const Queued& seed : *phase) {
      if (!flooder.has(seed.node, reachedState)) {
        flooder.set(seed.node, reachedState);
        phaseFrontier.push_back(seed);
      }
    }
    flooder.forwardFlood(phaseFrontier, reachedState, model.headroomCells, false, true);
    if (phase == &first && !first.empty()) {
      flooder.forEachNode([&](uint32_t node) {
        if (flooder.has(node, reachedState)) {
          flooder.set(node, fromSafeState);
        }
      });
    }
  }
  const double containmentSeconds = secondsSince(containmentStart);
  reportProgress("flooding the lane", 2, 4);

  std::vector<std::pair<uint32_t, uint32_t>> teleports;
  std::vector<Queued> escapes = seeds;
  for (const auto& pair : flooder.nodeLines) {
    if (flooder.has(pair.first, reachedState)) {
      if ((model.shape[pair.first] & absorbedShape) && flooder.landing[pair.second] >= 0) {
        teleports.emplace_back(static_cast<uint32_t>(flooder.landing[pair.second]), pair.first);
      }
      escapes.push_back({pair.first, flooder.columnOfNode(pair.first)});
    }
  }
  for (const Event& event : flooder.events) {
    if (event.kind == absorbedEvent) {
      if (flooder.landing[event.line] >= 0) {
        teleports.emplace_back(static_cast<uint32_t>(flooder.landing[event.line]), event.from);
      }
      escapes.push_back({event.from, flooder.columnOfNode(event.from)});
    }
  }
  std::sort(teleports.begin(), teleports.end());

  const auto safeStart = std::chrono::steady_clock::now();
  if (input.safeSeed >= 0) {
    const uint32_t safeNode = static_cast<uint32_t>(seedNodes[input.safeSeed]);
    flooder.set(safeNode, toSafeState);
    flooder.reverseFlood({{safeNode, flooder.columnOfNode(safeNode)}}, toSafeState, model.headroomCells, teleports);
  }
  const double safeSeconds = secondsSince(safeStart);
  reportProgress("flooding the lane", 3, 4);

  const auto trapStart = std::chrono::steady_clock::now();
  std::vector<Queued> escapeFrontier;
  for (const Queued& entry : escapes) {
    if (!flooder.has(entry.node, markState)) {
      flooder.set(entry.node, markState);
      escapeFrontier.push_back(entry);
    }
  }
  flooder.reverseFlood(escapeFrontier, markState, model.headroomCells, teleports);
  std::atomic<uint32_t> traps(0), reached(0), reachedFull(0);
  flooder.forEachNode([&](uint32_t node) {
    const uint8_t value = flooder.state[node].load(std::memory_order_relaxed);
    if ((value & reachedState) && !(value & markState)) {
      flooder.set(node, trapState);
      ++traps;
    }
    if (value & reachedState) {
      ++reached;
    }
    if (value & reachedFullState) {
      ++reachedFull;
    }
  });
  const double trapSeconds = secondsSince(trapStart);
  reportProgress("flooding the lane", 4, 4);

  const auto writeStart = std::chrono::steady_clock::now();
  writeLane(lanePath, model, flooder, seedNodes);
  const double writeSeconds = secondsSince(writeStart);

  uint32_t wetNodes = 0;
  for (const uint8_t value : model.shape) {
    wetNodes += (value & wetShape) ? 1 : 0;
  }
  PROCESS_MEMORY_COUNTERS memory;
  std::memset(&memory, 0, sizeof(memory));
  GetProcessMemoryInfo(GetCurrentProcess(), &memory, sizeof(memory));
  std::ostringstream report;
  report << "{\"origin\": " << jsonVector(grid.origin) << ", \"cellSize\": " << jsonFloat(grid.cellSize) << ", \"cellHeight\": " << jsonFloat(grid.cellHeight)
    << ", \"tileCells\": " << grid.tileCells << ", \"tiles\": [" << grid.tilesX << ", " << grid.tilesZ << "], \"columns\": " << grid.columns
    << ", \"verticalCells\": " << grid.verticalCells << ", \"slabs\": " << grid.slabs
    << ", \"climbCells\": " << model.climb << ", \"playerCells\": " << model.playerCells << ", \"headroomCells\": " << model.headroomCells
    << ", \"nodes\": " << model.nodeCount() << ", \"wetNodes\": " << wetNodes << ", \"reached\": " << reached.load() << ", \"reachedFull\": " << reachedFull.load()
    << ", \"traps\": " << traps.load() << ", \"events\": " << flooder.events.size() << ", \"notFollowed\": [";
  for (size_t line = 0; line < notFollowed.size(); ++line) {
    report << (line ? ", " : "") << (notFollowed[line].empty() ? "null" : jsonString(notFollowed[line]));
  }
  report << "], \"threads\": " << pool.size()
    << ", \"seconds\": {\"rasterize\": " << rasterSeconds << ", \"fullFlood\": " << fullSeconds << ", \"containmentFlood\": " << containmentSeconds
    << ", \"toSafeFlood\": " << safeSeconds << ", \"trapFlood\": " << trapSeconds << ", \"write\": " << writeSeconds << ", \"total\": " << secondsSince(start) << "}"
    << ", \"peakWorkingSetBytes\": " << memory.PeakWorkingSetSize << ", \"peakPagefileBytes\": " << memory.PeakPagefileUsage << "}";
  writeText(reportPath, report.str());
}
