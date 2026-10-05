"""The player's lane in plan: where players reach, by label (walk green, swim blue, slide orange, squeeze violet, traps hatched orange),
water the design player wades lined blue over its floor's fill, walkable floor they never reach cross-hatched dark, columns reached at a
level under a higher one or under water standing clear of it dotted (black where the design player fits there, violet where only a
smaller one does), ledges (reached walkable floors side by side more than the step apart, where the plan resolves columns) black, leak edges (onto nothing or under the
underworld) magenta, the edges where a zone line catches a fall green, zone lines filled green when reached and dashed when not, swim
boxes dashed over the fill, seeds as white arrows (hollow when isolated), plot entrances as ticks (a red X when unreached), region
outlines in their access colors, and the problems ringed black and numbered, each number by its ring or led to it by a line; with a
legend band below. LanePlan lays it over planDrawing.drawPlan's plan; drawLaneBesideNav draws a lane from server files
over the floors it reaches beside the server's NPC nav."""
import math

import numpy
from PIL import Image, ImageDraw

import planDrawing
import playerReach
import playerScale
import serverMapDrawing

labelColors = {
  playerReach.walkLabel: (35, 165, 60), playerReach.swimLabel: (25, 105, 225), playerReach.slideLabel: (240, 140, 20),
  playerReach.squeezeLabel: (150, 65, 205), playerReach.zoneLineLabel: (20, 140, 40),
}
fillAlpha = 125
hatchAlpha = 215
hatchSpacing = 8
trapColor = (240, 120, 0)
unreachedColor = (30, 30, 30)
layeredColor = (25, 25, 25)
squeezeLayeredColor = labelColors[playerReach.squeezeLabel]
layeredSpacing = 6
voidColor = (215, 0, 215)
catchColor = planDrawing.zoneLineColor
edgeWidth = 4
zoneLineColor = planDrawing.zoneLineColor
seedFill = (255, 255, 255)
seedOutline = (20, 20, 20)
seedLength = 20
seedWidth = 18
plotColor = planDrawing.plotColor
unreachedPlotColor = (220, 20, 20)
plotMark = 7
problemColor = (15, 15, 15)
problemHalo = (255, 255, 255)
problemRing = 9
# A problem on an entry is ringed round the entry's arrow, so the arrow shows whole.
entryRing = seedLength + 6
problemNumberSize = 17
# A problem's number set further than this from its ring is led back to it by a line.
leaderReach = 2 * problemRing
regionColors = {"play": planDrawing.regionColor, "view": planDrawing.accessColors["view"], "none": planDrawing.accessColors["none"], None: planDrawing.accessColors[None]}
legendRowHeight = 22
legendSwatch = 18
# Legend columns are as wide as the widest entry and this much apart.
legendColumnGap = 20
legendPadding = 8
legendTextSize = 14
legendBackground = (250, 250, 247)
legendTextColor = (20, 20, 20)
# The relief runs between these greys, its highest kept apart from the background.
reliefLow, reliefHigh = (70, 66, 60), (200, 196, 186)
chunkNodes = 1 << 22
trapFlag, layeredFlag, unreachedFlag, squeezeLayeredFlag, wadedFlag = 1, 2, 4, 8, 16
# Water players wade: lines across it in the swim blue, this far apart.
wadedColor = labelColors[playerReach.swimLabel]
wadedSpacing = 6
swimBoxHalo = (255, 255, 255)
# Where reached walkable floors in columns side by side lie more than the step apart, a line along their shared edge, drawn where the
# plan resolves columns.
ledgeColor = (20, 20, 20)


class LanePlan:
  """A lane drawn over a plan: fill (the labels, hatches, dots, and ledges as one layer), strokes (void and catch edges, zone lines, seeds, plot
  entrances, region outlines, problem rings), labels (problem numbers, seed and region names), and its legend. level {bottom, top} keeps
  only nodes whose height lies in it. Drawn as a layer of another plan (complete false), the problems and the regions, which that plan
  draws its own way, are left out."""

  def __init__(self, analysis, description, level, complete):
    nodes = analysis["nodes"]
    self.lane = lane = nodes.lane
    self.analysis, self.description, self.complete = analysis, description, complete
    self.regions = description["regions"] if complete else []
    inBand = numpy.ones(lane.nodeCount, dtype=bool) if level is None else (nodes.height >= level["bottom"]) & (nodes.height <= level["top"])
    self.label = numpy.zeros(lane.columns, dtype=numpy.uint8)
    self.flags = numpy.zeros(lane.columns, dtype=numpy.uint8)
    self.floorTop = numpy.zeros(lane.columns, dtype=numpy.uint16)
    layered = numpy.zeros(lane.columns, dtype=bool)
    squeezeLayered = numpy.zeros(lane.columns, dtype=bool)
    unreached = numpy.zeros(lane.columns, dtype=bool)
    carried = None
    # A column's nodes lie together, lowest first, each floor before the water over it, so the last reached one of a run is the
    # column's top; a column split between two chunks has its higher nodes in the later one, which writes over the earlier, and its lower
    # level carried over. Water the design player wades is drawn by the floor under it; water standing clear of the floor and the water
    # under it (a floating box) is a level of its own.
    held = None
    for start in range(0, lane.nodeCount, chunkNodes):
      part = slice(start, min(lane.nodeCount, start + chunkNodes))
      flat = nodes.z[part].astype(numpy.int32) * lane.columnsX + nodes.x[part]
      band = inBand[part]
      wet = nodes.wet[part]
      floating, held = floatingWater(flat, wet, nodes.low[part], nodes.high[part], held)
      reached = numpy.flatnonzero(nodes.reached[part] & band & ~(nodes.wet[part] & nodes.shallow[part]))
      if len(reached):
        columns = flat[reached]
        top = numpy.append(columns[1:] != columns[:-1], True)
        self.label[columns[top]] = nodes.label[part][reached[top]]
        self.floorTop[columns[top]] = numpy.where(wet[reached[top]] | ~nodes.walkable[part][reached[top]], 0, nodes.low[part][reached[top]])
        waded = ~nodes.wet[part] & (nodes.box[part] >= 0) & nodes.shallow[part]
        self.flags[columns[top]] = (numpy.where(nodes.trap[part][reached[top]], trapFlag, 0) | numpy.where(waded[reached[top]], wadedFlag, 0)).astype(numpy.uint8)
        levels = reached[~wet[reached] | floating[reached]]
        floors = flat[levels]
        if len(floors):
          full = (nodes.state[part][levels] & playerReach.reachedFullState) != 0
          if carried is not None and carried[0] == floors[0]:
            (layered if carried[1] else squeezeLayered)[floors[0]] = True
          lower = numpy.append(floors[1:] == floors[:-1], False)
          layered[floors[lower & full]] = True
          squeezeLayered[floors[lower & ~full]] = True
          carried = (floors[-1], bool(full[-1]))
      walkable = nodes.walkable[part] & ~nodes.wet[part] & ((nodes.shape[part] & playerReach.absorbedShape) == 0) & band
      unreached[flat[walkable]] = True
    self.flags[layered] |= layeredFlag
    self.flags[squeezeLayered & ~layered] |= squeezeLayeredFlag
    del layered, squeezeLayered
    unreached[self.label > 0] = False
    self.flags[unreached] |= unreachedFlag
    self.label = self.label.reshape(lane.columnsZ, lane.columnsX)
    self.flags = self.flags.reshape(lane.columnsZ, lane.columnsX)
    self.floorTop = self.floorTop.reshape(lane.columnsZ, lane.columnsX)
    self.fromColumns = (nodes.x[lane.events[:, 1]].astype(numpy.int64), nodes.z[lane.events[:, 1]].astype(numpy.int64)) if len(lane.events) else None

  def sample(self, frame, size):
    """The columns under each pixel's center: (inside, row, column) arrays over the drawing."""
    lane = self.lane
    across = (numpy.arange(size[0]) + 0.5) / size[0]
    down = (numpy.arange(size[1]) + 0.5) / size[1]
    zoneY = frame.center[1] + frame.width / 2 - across * frame.width
    zoneX = frame.center[0] + frame.height / 2 - down * frame.height
    columnX = numpy.floor((zoneY - lane.origin[0]) / lane.cellSize).astype(numpy.int64)
    columnZ = numpy.floor((zoneX - lane.origin[2]) / lane.cellSize).astype(numpy.int64)
    insideX = (columnX >= 0) & (columnX < lane.columnsX)
    insideZ = (columnZ >= 0) & (columnZ < lane.columnsZ)
    inside = insideZ[:, None] & insideX[None, :]
    return inside, numpy.clip(columnZ, 0, lane.columnsZ - 1)[:, None], numpy.clip(columnX, 0, lane.columnsX - 1)[None, :]

  def fill(self, frame, size):
    inside, rows, columns = self.sample(frame, size)
    label = numpy.where(inside, self.label[rows, columns], 0)
    flags = numpy.where(inside, self.flags[rows, columns], 0)
    pixels = numpy.zeros((size[1], size[0], 4), dtype=numpy.uint8)
    for value, color in labelColors.items():
      pixels[label == value] = (*color, fillAlpha)
    down, across = numpy.indices((size[1], size[0]))
    trapHatch = ((across + down) % hatchSpacing < 2) & ((flags & trapFlag) != 0)
    pixels[trapHatch] = (*trapColor, hatchAlpha)
    crossing = ((across - down) % hatchSpacing == 0) | ((across + down) % hatchSpacing == 0)
    pixels[crossing & ((flags & unreachedFlag) != 0)] = (*unreachedColor, hatchAlpha)
    waves = (down % wadedSpacing < 2) & ((flags & wadedFlag) != 0)
    pixels[waves] = (*wadedColor, hatchAlpha)
    dots = (across % layeredSpacing == 0) & (down % layeredSpacing == 0)
    pixels[dots & ((flags & layeredFlag) != 0)] = (*layeredColor, 255)
    pixels[dots & ((flags & squeezeLayeredFlag) != 0)] = (*squeezeLayeredColor, 255)
    floors = numpy.where(inside, self.floorTop[rows, columns], 0).astype(numpy.int32)
    climb = playerScale.stepHeight / self.lane.cellHeight
    ledge = numpy.zeros(floors.shape, dtype=bool)
    for axis in (0, 1):
      first, second = (floors[:-1], floors[1:]) if axis == 0 else (floors[:, :-1], floors[:, 1:])
      neighbours = numpy.abs(numpy.diff(rows[:, 0]))[:, None] == 1 if axis == 0 else numpy.abs(numpy.diff(columns[0]))[None, :] == 1
      apart = neighbours & (first > 0) & (second > 0) & (numpy.abs(second - first) > climb)
      if axis == 0:
        ledge[:-1] |= apart
        ledge[1:] |= apart
      else:
        ledge[:, :-1] |= apart
        ledge[:, 1:] |= apart
    pixels[ledge] = (*ledgeColor, 255)
    return Image.fromarray(pixels, "RGBA")

  def surface(self):
    """Each column's surface, (columnsZ, columnsX): the height of its highest reached node, else of its highest node, NaN where it has
    none."""
    nodes, lane = self.analysis["nodes"], self.lane
    surface = numpy.full(lane.columns, numpy.nan, dtype=numpy.float32)
    for onlyReached in (False, True):
      for start in range(0, lane.nodeCount, chunkNodes):
        part = slice(start, min(lane.nodeCount, start + chunkNodes))
        chosen = numpy.flatnonzero(nodes.reached[part]) if onlyReached else numpy.arange(part.stop - part.start)
        flat = nodes.z[part][chosen].astype(numpy.int32) * lane.columnsX + nodes.x[part][chosen]
        top = numpy.append(flat[1:] != flat[:-1], True)
        surface[flat[top]] = nodes.height[part][chosen[top]]
    return surface.reshape(lane.columnsZ, lane.columnsX)

  def relief(self, frame, size):
    """The floors players walk as grey relief: each column's surface, lighter higher (between the 1st and 99th percentile of what the
    drawing holds), shaded by its slope away from a light in the north-west; the planDrawing background where the lane holds nothing."""
    inside, rows, columns = self.sample(frame, size)
    heights = numpy.where(inside, self.surface()[rows, columns], numpy.nan)
    image = numpy.empty((size[1], size[0], 3), dtype=numpy.float64)
    image[:] = serverMapDrawing.backgroundColor
    covered = numpy.isfinite(heights)
    if covered.any():
      low, high = numpy.percentile(heights[covered], (1, 99))
      share = numpy.clip((heights - low) / max(high - low, 1e-9), 0, 1)
      color = numpy.asarray(reliefLow) + (numpy.asarray(reliefHigh) - numpy.asarray(reliefLow)) * share[..., None]
      unitsPerPixel = frame.width / size[0]
      filled = numpy.where(covered, heights, heights[covered].mean())
      downSlope, acrossSlope = numpy.gradient(filled, unitsPerPixel)
      normal = numpy.stack([-acrossSlope, -downSlope, numpy.ones_like(filled)], axis=-1)
      normal /= numpy.linalg.norm(normal, axis=-1, keepdims=True)
      # In pixels the light comes from the upper left (the game's north-west): x right, y down, z up.
      light = numpy.array([-1.0, -1.0, 1.5]) / math.sqrt(4.25)
      shade = 0.55 + 0.45 * numpy.clip(normal @ light, 0, 1)
      image[covered] = (color * shade[..., None])[covered]
    return Image.fromarray(numpy.round(image).astype(numpy.uint8), "RGB")

  def inFrame(self, frame, point):
    x, y = frame.pixel(point)
    return 0 <= x < frame.size[0] and 0 <= y < frame.size[1]

  def strokes(self, draw, frame):
    lane = self.lane
    for region in self.regions:
      points = [frame.pixel(point) for point in region["outline"]]
      planDrawing.dashedLine(draw, points + points[:1], (*regionColors[region["access"]], 255), 3)
    for box in self.description["swimBoxes"]:
      low, high = box["low"], box["high"]
      corners = [frame.pixel(point) for point in ((low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1]))]
      draw.line(corners + corners[:2], fill=(*swimBoxHalo, 255), width=5, joint="curve")
      planDrawing.dashedLine(draw, corners + corners[:1], (*planDrawing.swimColors[box["liquid"]], 255), 3)
    if self.fromColumns is not None:
      for (fromX, fromZ), event in zip(zip(*self.fromColumns), lane.events):
        x, z = lane.columnPlaces(numpy.array([event[2]], dtype=numpy.int64))
        if (int(fromX), int(fromZ)) == (int(x[0]), int(z[0])):
          continue
        start, end = edgeBetween(lane, (int(fromX), int(fromZ)), (int(x[0]), int(z[0])))
        color = catchColor if event[0] == playerReach.absorbedEvent else voidColor
        draw.line([frame.pixel(start), frame.pixel(end)], fill=(*color, 255), width=edgeWidth)
    for line, reported in zip(self.description["zoneLines"], self.analysis["zoneLines"]):
      low, high = line["low"], line["high"]
      corners = [frame.pixel(point) for point in ((low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1]))]
      if reported["reached"]:
        draw.polygon(corners, fill=(*zoneLineColor, 150), outline=(*zoneLineColor, 255), width=3)
      else:
        planDrawing.dashedLine(draw, corners + corners[:1], (*zoneLineColor, 255), 3)
    for plot in self.analysis["plotEntrances"]:
      x, y = frame.pixel(plot["at"])
      if plot["reached"]:
        draw.line([(x - plotMark, y), (x + plotMark, y)], fill=(*plotColor, 255), width=3)
        draw.line([(x, y - plotMark), (x, y + plotMark)], fill=(*plotColor, 255), width=3)
      else:
        draw.line([(x - plotMark, y - plotMark), (x + plotMark, y + plotMark)], fill=(*unreachedPlotColor, 255), width=4)
        draw.line([(x - plotMark, y + plotMark), (x + plotMark, y - plotMark)], fill=(*unreachedPlotColor, 255), width=4)
    for seed, entry in zip(self.description["seeds"], self.analysis["entries"]):
      drawSeed(draw, frame.pixel(entry["at"]), seed.get("headingDegrees"), seed["isolated"])
    if self.complete:
      for problem in self.problemsIn(frame):
        drawRing(draw, frame.pixel(problem["center"]), ringRadius(problem))

  def problemsIn(self, frame):
    return [problem for problem in self.analysis["problems"] if self.inFrame(frame, problem["center"])]

  def labels(self, board, frame):
    """Name what the lane marks: every ring, seed, and plot mark is kept clear of labels where it can be, then each problem's number is
    set by its ring (led back to it by a line when it lands far off), each seed's name beside it, and each region's name just above its
    outline where its outline runs through the drawing and enough of it shows to hold its name."""
    problems = self.problemsIn(frame) if self.complete else []
    for problem in problems:
      x, y = frame.pixel(problem["center"])
      radius = ringRadius(problem)
      board.avoid([x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2])
    for entry in self.analysis["entries"]:
      x, y = frame.pixel(entry["at"])
      board.avoid([x - seedLength, y - seedLength, x + seedLength, y + seedLength])
    for plot in self.analysis["plotEntrances"]:
      x, y = frame.pixel(plot["at"])
      board.avoid([x - plotMark - 2, y - plotMark - 2, x + plotMark + 2, y + plotMark + 2])
    for problem in problems:
      x, y = frame.pixel(problem["center"])
      radius = ringRadius(problem)
      wanted = (x + radius + 6, y - radius - 6)
      text = str(problem["number"])
      spot = board.clearSpot(wanted, text, problemNumberSize, "lb")
      spot = wanted if spot is None else spot
      board.write(spot, text, problemColor, problemNumberSize, "lb")
      if math.dist(spot, wanted) > leaderReach:
        box = board.textBox(spot, text, problemNumberSize, "lb")
        nearest = (min(max(x, box[0]), box[2]), min(max(y, box[1]), box[3]))
        reach = math.dist((x, y), nearest)
        if reach > radius:
          board.draw.line([(x + (nearest[0] - x) * radius / reach, y + (nearest[1] - y) * radius / reach), nearest], fill=(*problemColor, 255), width=2)
    ringed = {problem["entry"] for problem in problems if problem["kind"] == "disconnectedEntry"}
    for entry in self.analysis["entries"]:
      x, y = frame.pixel(entry["at"])
      reach = (entryRing if entry["name"] in ringed else seedLength) + 6
      board.place((x + reach, y), "S" if entry["kind"] == "safePoint" else entry["name"], seedOutline, 13, "lm")
    for region in self.regions:
      points = [frame.pixel(point) for point in region["outline"]]
      pieces = outlineInFrame(points, frame.size)
      if pieces and planDrawing.showsName(board, planDrawing.clippedToFrame(points, frame.size), region["name"], 13):
        highest = min((point for piece in pieces for point in piece), key=lambda point: point[1])
        board.place((highest[0], max(highest[1] - 4, 18)), region["name"], regionColors[region["access"]], 13, "mb")

  def legend(self, frame=None, base=()):
    """The drawing's key, each entry (kind of mark, color, words): the labels and marks the lane drew, and, for a whole lane plan, each
    kind of problem with its numbers; then base, the entries of the plan under the lane (baseLegend), and which way the plan's axes run."""
    items = [
      ("fill", labelColors[playerReach.walkLabel], "walk"), ("fill", labelColors[playerReach.swimLabel], "swim"),
      ("waves", wadedColor, f"water waded: no deeper than {playerScale.minimumSwimDepth:g}"),
      ("fill", labelColors[playerReach.slideLabel], "slide (too steep to stand)"), ("fill", labelColors[playerReach.squeezeLabel], "squeeze (only a smaller player)"),
      ("fill", labelColors[playerReach.zoneLineLabel], "taken by a zone line"), ("hatch", trapColor, "trap (no way back)"),
      ("crossHatch", unreachedColor, "walkable, never reached"), ("dots", layeredColor, "reached under a higher level"),
      ("dots", squeezeLayeredColor, "only a smaller player under it"), ("line", voidColor, "leak edge (onto nothing or under the underworld)"),
      ("line", ledgeColor, f"ledge: walked floors more than the step ({playerScale.stepHeight:g}) apart"),
      ("line", catchColor, "a zone line catches the fall"), ("box", zoneLineColor, "zone line reached"), ("dashedBox", zoneLineColor, "zone line not reached"),
      ("swimBox", planDrawing.swimColors["water"], "swim box (lava's magenta)"),
      ("arrow", seedOutline, "seed: where players arrive"), ("hollowArrow", seedOutline, "isolated seed"), ("plus", plotColor, "plot entrance reached"),
      ("cross", unreachedPlotColor, "plot entrance not reached"),
    ]
    if self.complete:
      items += [("dashed", regionColors[access], f"{access or 'undecided'} region") for access in ("play", "view", "none", None)]
      byKind = {}
      for problem in (self.analysis["problems"] if frame is None else self.problemsIn(frame)):
        byKind.setdefault(problem["kind"], []).append(problem["number"])
      items += [("ring", problemColor, f"{numberRuns(numbers)} {kind}") for kind, numbers in byKind.items()]
    return items + list(base) + [("text", legendTextColor, "plan: +X (north) up, +Y left")]


def baseLegend(layers):
  """The legend entries of a plan drawn under a lane: its relief, and its water and boundaries when it draws those layers."""
  items = [("relief", None, "relief: lighter is higher")]
  if "water" in layers:
    items.append(("fill", planDrawing.waterColor, "water surface"))
  if "boundaries" in layers:
    items.append(("line", planDrawing.boundaryColor, "boundary wall (lids and floors faintly filled)"))
  return items


def ringRadius(problem):
  return entryRing if problem["kind"] == "disconnectedEntry" else problemRing


def outlineInFrame(points, size):
  """The pieces of a closed outline (pixel points) that lie inside a drawing of size, each [start, end]."""
  pieces = []
  for start, end in zip(points, points[1:] + points[:1]):
    enter, leave = 0.0, 1.0
    for axis in (0, 1):
      delta = end[axis] - start[axis]
      if delta == 0:
        if not 0 <= start[axis] <= size[axis]:
          enter, leave = 1.0, 0.0
        continue
      first, second = -start[axis] / delta, (size[axis] - start[axis]) / delta
      enter, leave = max(enter, min(first, second)), min(leave, max(first, second))
    if enter < leave:
      pieces.append([(start[0] + (end[0] - start[0]) * share, start[1] + (end[1] - start[1]) * share) for share in (enter, leave)])
  return pieces


def floatingWater(flat, wet, low, high, held):
  """Which of a run of nodes (their columns, wetness, and lows and highs in cells, in lane order) are water standing clear of what holds
  it up in its column: its floor and the water under it there. held is (column, height) carried from the run before, or None; returns the
  flags and what to carry on."""
  count = len(flat)
  newColumn = numpy.ones(count, dtype=bool)
  newColumn[1:] = flat[1:] != flat[:-1]
  if held is not None:
    newColumn[0] = held[0] != flat[0]
  segment = numpy.cumsum(~wet | newColumn).astype(numpy.int64)
  span = 1 << 16
  running = numpy.maximum.accumulate(segment * span + numpy.where(wet, high, low).astype(numpy.int64))
  support = numpy.full(count, -1, dtype=numpy.int64)
  continued = numpy.zeros(count, dtype=bool)
  continued[1:] = segment[1:] == segment[:-1]
  support[continued] = running[:-1][continued[1:]] - segment[continued] * span
  if held is not None:
    carried = segment == 0
    support[carried] = numpy.maximum(support[carried], held[1])
  floating = wet & (low.astype(numpy.int64) > support)
  return floating, (int(flat[-1]), int(running[-1] - segment[-1] * span))


def numberRuns(numbers):
  """Numbers as runs: 1, 3-5, 8."""
  runs, start = [], None
  for index, number in enumerate(numbers):
    if start is None:
      start = number
    if index + 1 == len(numbers) or numbers[index + 1] != number + 1:
      runs.append(str(start) if start == number else f"{start}-{number}")
      start = None
  return ", ".join(runs)


def drawRing(draw, at, radius):
  x, y = at
  draw.ellipse([x - radius - 1, y - radius - 1, x + radius + 1, y + radius + 1], outline=(*problemHalo, 255), width=5)
  draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=(*problemColor, 255), width=3)


def edgeBetween(lane, first, second):
  """The plan segment (zone x, y) between two side by side columns, given as (x, z) indices along Recast x (zone y) and z (zone x)."""
  if first[0] != second[0]:
    zoneY = lane.origin[0] + max(first[0], second[0]) * lane.cellSize
    zoneX = lane.origin[2] + first[1] * lane.cellSize
    return (zoneX, zoneY), (zoneX + lane.cellSize, zoneY)
  zoneX = lane.origin[2] + max(first[1], second[1]) * lane.cellSize
  zoneY = lane.origin[0] + first[0] * lane.cellSize
  return (zoneX, zoneY), (zoneX, zoneY + lane.cellSize)


def drawSeed(draw, at, headingDegrees, isolated):
  """A seed: a white arrow along its heading (a white disc without one), hollow when isolated."""
  x, y = at
  if headingDegrees is None:
    box = [x - seedWidth / 2, y - seedWidth / 2, x + seedWidth / 2, y + seedWidth / 2]
    draw.ellipse(box, fill=None if isolated else (*seedFill, 255), outline=(*seedOutline, 255), width=2)
    return
  ahead = planDrawing.pixelDirection(headingDegrees)
  side = (-ahead[1], ahead[0])
  tip = (x + ahead[0] * seedLength, y + ahead[1] * seedLength)
  back = (x - ahead[0] * seedLength / 2, y - ahead[1] * seedLength / 2)
  arrow = [tip, (back[0] + side[0] * seedWidth / 2, back[1] + side[1] * seedWidth / 2), (back[0] - side[0] * seedWidth / 2, back[1] - side[1] * seedWidth / 2)]
  draw.polygon(arrow, fill=None if isolated else (*seedFill, 255), outline=(*seedOutline, 255), width=2)


def drawLegendItem(draw, left, top, item):
  """One legend entry at (left, top): its mark in a swatch, then its words."""
  kind, color, text = item
  right, bottom = left + legendSwatch, top + legendSwatch
  middle = (top + bottom) / 2
  if kind == "fill":
    draw.rectangle([left, top, right, bottom], fill=(*color, 255), outline=serverMapDrawing.darker(color))
  elif kind in ("hatch", "crossHatch"):
    draw.rectangle([left, top, right, bottom], outline=(170, 170, 170))
    for offset in range(-legendSwatch + 3, legendSwatch, 6):
      start, end = max(offset, 0), min(offset + legendSwatch, legendSwatch)
      draw.line([(left + start, bottom - (start - offset)), (left + end, bottom - (end - offset))], fill=color, width=2 if kind == "hatch" else 1)
      if kind == "crossHatch":
        draw.line([(left + start, top + (start - offset)), (left + end, top + (end - offset))], fill=color, width=1)
  elif kind == "dots":
    draw.rectangle([left, top, right, bottom], outline=(170, 170, 170))
    for dx in range(3, legendSwatch, layeredSpacing):
      for dy in range(3, legendSwatch, layeredSpacing):
        draw.rectangle([left + dx, top + dy, left + dx + 1, top + dy + 1], fill=color)
  elif kind == "line":
    draw.line([(left, middle), (right, middle)], fill=color, width=edgeWidth)
  elif kind == "dashed":
    planDrawing.dashedLine(draw, [(left, middle), (right, middle)], color, 3)
  elif kind == "box":
    draw.rectangle([left + 2, top + 2, right - 2, bottom - 2], fill=tuple(round(channel * 0.6 + 255 * 0.4) for channel in color), outline=color, width=3)
  elif kind in ("dashedBox", "swimBox"):
    corners = [(left + 2, top + 2), (right - 2, top + 2), (right - 2, bottom - 2), (left + 2, bottom - 2)]
    if kind == "swimBox":
      draw.rectangle([left, top, right, bottom], fill=(205, 205, 205))
      draw.rectangle([left + 2, top + 2, right - 2, bottom - 2], outline=swimBoxHalo, width=5)
    for start, end in zip(corners, corners[1:] + corners[:1]):
      draw.line([start, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)], fill=color, width=3)
  elif kind == "relief":
    for step in range(legendSwatch + 1):
      share = step / legendSwatch
      draw.line([(left + step, top), (left + step, bottom)], fill=tuple(round(low + (high - low) * share) for low, high in zip(reliefLow, reliefHigh)))
  elif kind == "waves":
    draw.rectangle([left, top, right, bottom], fill=(*labelColors[playerReach.walkLabel],), outline=serverMapDrawing.darker(labelColors[playerReach.walkLabel]))
    for y in range(top + 2, bottom, wadedSpacing):
      draw.line([(left, y), (right, y)], fill=color, width=2)
  elif kind in ("arrow", "hollowArrow"):
    draw.polygon([(right, middle), (left + 2, top + 2), (left + 2, bottom - 2)], fill=None if kind == "hollowArrow" else seedFill, outline=color, width=2)
  elif kind == "plus":
    draw.line([(left + 2, middle), (right - 2, middle)], fill=color, width=3)
    draw.line([((left + right) / 2, top + 2), ((left + right) / 2, bottom - 2)], fill=color, width=3)
  elif kind == "cross":
    draw.line([(left + 2, top + 2), (right - 2, bottom - 2)], fill=color, width=3)
    draw.line([(left + 2, bottom - 2), (right - 2, top + 2)], fill=color, width=3)
  elif kind == "ring":
    draw.ellipse([left + 1, top + 1, right - 1, bottom - 1], outline=color, width=3)
  elif kind != "text":
    raise ValueError(f"No legend mark '{kind}'")
  draw.text((left if kind == "text" else right + 8, top), text, fill=legendTextColor, font=planDrawing.fontOf(legendTextSize))


def legendLayout(width, items):
  """Where each legend entry goes in a band `width` wide: rows of as many columns as fit, each as wide as the widest entry; (places,
  height)."""
  font = planDrawing.fontOf(legendTextSize)
  column = max(font.getlength(text) + (0 if kind == "text" else legendSwatch + 8) for kind, _, text in items) + legendColumnGap
  columns = max(1, int((width - 2 * legendPadding) // column))
  places = [(legendPadding + (index % columns) * column, legendPadding + (index // columns) * legendRowHeight) for index in range(len(items))]
  rows = -(-len(items) // columns)
  return places, 2 * legendPadding + rows * legendRowHeight


def withLegend(image, items):
  """The image with a band under it holding the legend's entries."""
  places, height = legendLayout(image.width, items)
  sheet = Image.new("RGB", (image.width, image.height + height), legendBackground)
  sheet.paste(image.convert("RGB"), (0, 0))
  draw = ImageDraw.Draw(sheet)
  for (left, top), item in zip(places, items):
    drawLegendItem(draw, left, image.height + top, item)
  return sheet


def legendUnder(path, lanePlan, frame, layers):
  """Add the lane's legend for the plan drawn at path in frame (LanePlan.legend), with the entries of the plan's own layers under it
  (baseLegend), as a band under it."""
  legendBand(path, lanePlan.legend(frame, baseLegend(layers)))


def legendBand(path, items):
  """Add a band holding the legend's entries under the picture at path."""
  with Image.open(path) as opened:
    picture = opened.convert("RGB")
  withLegend(picture, items).save(path)


def ringProblem(image, share, fieldOfViewDegrees, number):
  """Ring a problem in the middle of a view looking at it: the ring as wide as the problem (share, its radius over its distance) in a
  view of fieldOfViewDegrees vertically, numbered beside it; a problem wider than the view is ringed at its center only."""
  draw = ImageDraw.Draw(image)
  radius = share / math.tan(math.radians(fieldOfViewDegrees) / 2) * image.height / 2
  radius = problemRing * 2 if radius > 0.45 * image.height else max(problemRing * 2, radius)
  center = (image.width / 2, image.height / 2)
  drawRing(draw, center, radius)
  planDrawing.labelAt(draw, (center[0] + radius * 0.71 + 4, center[1] - radius * 0.71 - 4), str(number), problemColor, 22, "lb")


def drawLaneBesideNav(outputPath, serverTriangles, lanePlan, inspection, titles, panelWidth=900):
  """A lane from a zone's server files over the floors it reaches (LanePlan.relief: the highest reached floor of each column, so a lid or
  ceiling over the pass hides nothing) and, on the same frame, the server's NPC nav by component (serverMapDrawing.componentPanel of
  inspection), side by side, each titled and with its legend. Written as PNG; returns the sheet's size, the frame, and the legends."""
  frame = serverMapDrawing.collisionFrame(serverTriangles, panelWidth)
  left = lanePanel(frame, lanePlan.relief(frame, frame.size), lanePlan)
  navPanel = serverMapDrawing.componentPanel(titles[1], inspection)
  right, _ = serverMapDrawing.drawNavPanel(navPanel, frame)
  relief = [("fill", reliefHigh, "relief: the highest reached floor, else the highest floor"), ("fill", serverMapDrawing.backgroundColor, "no floor at all")]
  legends = [relief + lanePlan.legend(frame), [("fill", color, text) for color, text in navPanel["legend"]]]
  layouts = [legendLayout(frame.size[0], legend) for legend in legends]
  gap = serverMapDrawing.panelGap
  legendHeight = max(height for _, height in layouts)
  sheet = Image.new("RGB", (2 * frame.size[0] + 3 * gap, serverMapDrawing.titleHeight + frame.size[1] + legendHeight + gap), (255, 255, 255))
  draw = ImageDraw.Draw(sheet)
  for index, (panel, title, legend, (places, _)) in enumerate(zip((left, right), titles, legends, layouts)):
    corner = gap + index * (frame.size[0] + gap)
    sheet.paste(panel.convert("RGB"), (corner, serverMapDrawing.titleHeight))
    draw.text((corner, gap), title, fill=serverMapDrawing.titleColor, font=planDrawing.fontOf(17))
    for (x, y), item in zip(places, legend):
      drawLegendItem(draw, corner + x, serverMapDrawing.titleHeight + frame.size[1] + y, item)
  sheet.save(outputPath, "PNG")
  return {"width": sheet.width, "height": sheet.height, "frame": {"center": list(frame.center), "width": frame.width, "size": list(frame.size)}, "legends": [[text for _, _, text in legend] for legend in legends]}


def lanePanel(frame, base, lanePlan):
  """A lane over a base image in a frame, with the grid, scale and north: the image."""
  image = Image.alpha_composite(base.convert("RGBA"), lanePlan.fill(frame, frame.size))
  layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
  draw = ImageDraw.Draw(layer)
  planDrawing.drawGrid(draw, frame)
  lanePlan.strokes(draw, frame)
  board = planDrawing.LabelBoard(draw, frame.size)
  planDrawing.labelGrid(board, frame)
  planDrawing.drawScale(board, frame, planDrawing.gridStepFor(frame.width))
  lanePlan.labels(board, frame)
  return Image.alpha_composite(image, layer)
