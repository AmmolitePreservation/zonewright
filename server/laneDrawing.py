"""The player's lane in plan: where players reach, by label (walk green, swim blue, slide orange, squeeze violet, traps hatched orange),
walkable floor they never reach cross-hatched dark, columns reached at a level under a higher one dotted (black where the design player
fits there, violet where only a smaller one does), ledges (reached floors side by side more than a step apart) dark, leak edges (onto
nothing or under the underworld) magenta, the edges where a zone line catches a fall green, zone lines hatched green when reached and
dashed when not, seeds as white arrows (hollow when isolated), plot entrances as ticks (a red X when unreached), region outlines in their
access colors, and the problems ringed black (round the mark they lie on) and numbered over their rings or led to them by a line; with a
legend band below of what the drawing shows. LanePlan lays it over planDrawing.drawPlan's plan; drawLaneBesideNav draws a lane from
server files over the floors it reaches beside the server's NPC nav."""
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
# The labels the plan fills; a zone line's own nodes show as its hatched box.
planFillLabels = (playerReach.walkLabel, playerReach.swimLabel, playerReach.slideLabel, playerReach.squeezeLabel)
hatchAlpha = 215
hatchSpacing = 8
trapColor = (240, 120, 0)
unreachedColor = (30, 30, 30)
# Floor never reached is tinted under its cross-hatch, so the grid's lines crossing it read as dimmed, never as dash-dot outlines.
unreachedFillAlpha = 70
layeredColor = (25, 25, 25)
squeezeLayeredColor = labelColors[playerReach.squeezeLabel]
layeredSpacing = 8
layeredDot = 2
ledgeColor = (45, 35, 25)
ledgeAlpha = 230
zoneLineHatchSpacing = 6
zoneLineFillAlpha = 60
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
ringRadii = {"disconnectedEntry": seedLength + 4, "unreachedPlot": 2 * plotMark}
nameGap = 5
# The band along the drawing's top edge that planDrawing.labelGrid writes the grid's coordinates in.
axisBand = 22
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
trapFlag, layeredFlag, unreachedFlag, squeezeLayeredFlag = 1, 2, 4, 8


class LanePlan:
  """A lane drawn over a plan: fill (the labels, hatches, dots, ledges, and, for a whole lane plan, the region outlines as one layer, so
  the plan's boundaries draw over the outlines), strokes (void and catch edges, zone lines, seeds, plot entrances, problem rings),
  labels (problem numbers, zone line, seed and region names), and its legend. level {bottom, top} keeps only nodes whose height lies in
  it. Drawn as a layer of another plan (complete false), the problems, the regions, and the zone lines' names, which that plan draws its
  own way, are left out."""

  def __init__(self, analysis, description, level, complete):
    nodes = analysis["nodes"]
    self.lane = lane = nodes.lane
    self.analysis, self.description, self.complete = analysis, description, complete
    self.regions = description["regions"] if complete else []
    inBand = numpy.ones(lane.nodeCount, dtype=bool) if level is None else (nodes.height >= level["bottom"]) & (nodes.height <= level["top"])
    self.label = numpy.zeros(lane.columns, dtype=numpy.uint8)
    self.flags = numpy.zeros(lane.columns, dtype=numpy.uint8)
    self.top = numpy.full(lane.columns, numpy.nan, dtype=numpy.float32)
    layered = numpy.zeros(lane.columns, dtype=bool)
    squeezeLayered = numpy.zeros(lane.columns, dtype=bool)
    unreached = numpy.zeros(lane.columns, dtype=bool)
    carried = None
    # A column's nodes lie together, lowest first, so the last reached one of a run is the column's top; a column split between two
    # chunks has its higher nodes in the later one, which writes over the earlier, and its lower floor carried over.
    for start in range(0, lane.nodeCount, chunkNodes):
      part = slice(start, min(lane.nodeCount, start + chunkNodes))
      flat = nodes.z[part].astype(numpy.int32) * lane.columnsX + nodes.x[part]
      band = inBand[part]
      reached = numpy.flatnonzero(nodes.reached[part] & band)
      if len(reached):
        columns = flat[reached]
        top = numpy.append(columns[1:] != columns[:-1], True)
        tops = reached[top]
        self.label[columns[top]] = nodes.label[part][tops]
        self.flags[columns[top]] = numpy.where(nodes.trap[part][tops], trapFlag, 0).astype(numpy.uint8)
        self.top[columns[top]] = numpy.where(nodes.wet[part][tops], numpy.nan, nodes.height[part][tops])
        dry = reached[~nodes.wet[part][reached]]
        floors = flat[dry]
        if len(floors):
          full = (nodes.state[part][dry] & playerReach.reachedFullState) != 0
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
    self.top = self.top.reshape(lane.columnsZ, lane.columnsX)
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

  def pixels(self, frame, size):
    """Each pixel's label and flags, and whether it lies on a ledge: where the dry floors two pixels side by side reach differ by more
    than the step (playerScale.stepHeight), the edge of a platform, roof, or pit the plan's fills alone would not show."""
    inside, rows, columns = self.sample(frame, size)
    label = numpy.where(inside, self.label[rows, columns], 0)
    flags = numpy.where(inside, self.flags[rows, columns], 0)
    top = numpy.where(inside, self.top[rows, columns], numpy.nan)
    ledge = numpy.zeros(label.shape, dtype=bool)
    across = numpy.abs(top[:, 1:] - top[:, :-1]) > playerScale.stepHeight
    down = numpy.abs(top[1:, :] - top[:-1, :]) > playerScale.stepHeight
    ledge[:, 1:] |= across
    ledge[:, :-1] |= across
    ledge[1:, :] |= down
    ledge[:-1, :] |= down
    return label, flags, ledge

  def fill(self, frame, size):
    label, flags, ledge = self.pixels(frame, size)
    pixels = numpy.zeros((size[1], size[0], 4), dtype=numpy.uint8)
    for value in planFillLabels:
      pixels[label == value] = (*labelColors[value], fillAlpha)
    down, across = numpy.indices((size[1], size[0]))
    trapHatch = ((across + down) % hatchSpacing < 2) & ((flags & trapFlag) != 0)
    pixels[trapHatch] = (*trapColor, hatchAlpha)
    unreached = (flags & unreachedFlag) != 0
    pixels[unreached] = (*unreachedColor, unreachedFillAlpha)
    crossing = ((across - down) % hatchSpacing == 0) | ((across + down) % hatchSpacing == 0)
    pixels[crossing & unreached] = (*unreachedColor, hatchAlpha)
    dots = (across % layeredSpacing < layeredDot) & (down % layeredSpacing < layeredDot)
    pixels[dots & ((flags & layeredFlag) != 0)] = (*layeredColor, 255)
    pixels[dots & ((flags & squeezeLayeredFlag) != 0)] = (*squeezeLayeredColor, 255)
    pixels[ledge] = (*ledgeColor, ledgeAlpha)
    image = Image.fromarray(pixels, "RGBA")
    draw = ImageDraw.Draw(image)
    for region in self.regions:
      points = [frame.pixel(point) for point in region["outline"]]
      planDrawing.dashedLine(draw, points + points[:1], (*regionColors[region["access"]], 255), 3)
    return image

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

  def edges(self, frame):
    """The leak and catch edges in plan: (start, end, absorbed) per event whose fall leaves its column."""
    lane = self.lane
    found = []
    if self.fromColumns is not None:
      for (fromX, fromZ), event in zip(zip(*self.fromColumns), lane.events):
        x, z = lane.columnPlaces(numpy.array([event[2]], dtype=numpy.int64))
        if (int(fromX), int(fromZ)) == (int(x[0]), int(z[0])):
          continue
        start, end = edgeBetween(lane, (int(fromX), int(fromZ)), (int(x[0]), int(z[0])))
        found.append((start, end, event[0] == playerReach.absorbedEvent))
    return found

  def zoneLineBoxes(self, frame):
    """Each zone line's box in the drawing's pixels (left, top, right, bottom), with its line and whether it is reached."""
    boxes = []
    for line, reported in zip(self.description["zoneLines"], self.analysis["zoneLines"]):
      (x0, y0), (x1, y1) = frame.pixel(line["high"][:2]), frame.pixel(line["low"][:2])
      boxes.append(((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), line, reported["reached"]))
    return boxes

  def strokes(self, draw, frame):
    for start, end, absorbed in self.edges(frame):
      draw.line([frame.pixel(start), frame.pixel(end)], fill=(*(catchColor if absorbed else voidColor), 255), width=edgeWidth)
    for box, _, reached in self.zoneLineBoxes(frame):
      left, top, right, bottom = box
      corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
      if reached:
        draw.rectangle(box, fill=(*zoneLineColor, zoneLineFillAlpha))
        hatchBox(draw, box, (*zoneLineColor, 255), zoneLineHatchSpacing)
        draw.rectangle(box, outline=(*zoneLineColor, 255), width=3)
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
    """Name what the lane marks: every ring, seed, and plot mark is kept clear of labels where it can be; for a whole lane plan each
    zone line is named in its box, which later labels keep clear of, and each problem's number set just over its ring (led back to it by
    a line when it lands far off); then each seed's name beside it, and each region's name just over the middle of its outline's top, or
    just under it where that lies in the grid's labels."""
    problems = self.problemsIn(frame) if self.complete else []
    rings = {}
    for problem in problems:
      x, y = frame.pixel(problem["center"])
      radius = ringRadius(problem)
      rings[problem["number"]] = radius
      board.avoid([x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2])
    reaches = []
    for entry in self.analysis["entries"]:
      x, y = frame.pixel(entry["at"])
      ringed = [rings[problem["number"]] for problem in problems if problem["kind"] == "disconnectedEntry" and problem.get("entry") == entry["name"]]
      reaches.append(max([seedLength] + ringed))
      board.avoid([x - seedLength, y - seedLength, x + seedLength, y + seedLength])
    for plot in self.analysis["plotEntrances"]:
      x, y = frame.pixel(plot["at"])
      board.avoid([x - plotMark - 2, y - plotMark - 2, x + plotMark + 2, y + plotMark + 2])
    if self.complete:
      boxes = [(box, line) for box, line, _ in self.zoneLineBoxes(frame) if box[2] > 0 and box[3] > 0 and box[0] < frame.size[0] and box[1] < frame.size[1]]
      for box, line in boxes:
        board.place(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), line["name"], zoneLineColor, 13)
      for box, _ in boxes:
        board.avoid(list(box))
    for problem in problems:
      x, y = frame.pixel(problem["center"])
      radius = rings[problem["number"]]
      wanted = (x, y - radius - 3)
      text = str(problem["number"])
      spot = board.clearSpot(wanted, text, problemNumberSize, "mb")
      spot = wanted if spot is None else spot
      board.write(spot, text, problemColor, problemNumberSize, "mb")
      if math.dist(spot, wanted) > leaderReach:
        box = board.textBox(spot, text, problemNumberSize, "mb")
        nearest = (min(max(x, box[0]), box[2]), min(max(y, box[1]), box[3]))
        reach = math.dist((x, y), nearest)
        if reach > radius:
          board.draw.line([(x + (nearest[0] - x) * radius / reach, y + (nearest[1] - y) * radius / reach), nearest], fill=(*problemColor, 255), width=2)
    for entry, reach in zip(self.analysis["entries"], reaches):
      x, y = frame.pixel(entry["at"])
      nameBeside(board, (x, y), reach, "S" if entry["kind"] == "safePoint" else entry["name"], seedOutline, 13)
    for region in self.regions:
      shown = planDrawing.clippedToFrame([frame.pixel(point) for point in region["outline"]], frame.size)
      if planDrawing.showsName(board, shown, region["name"], 13):
        highest = min(y for _, y in shown)
        along = [x for x, y in shown if y <= highest + 1]
        middle = (min(along) + max(along)) / 2
        above = board.textBox((middle, highest - 4), region["name"], 13, "mb")
        if above[1] >= axisBand:
          board.place((middle, highest - 4), region["name"], regionColors[region["access"]], 13, "mb")
        else:
          board.place((middle, highest + 4), region["name"], regionColors[region["access"]], 13, "ma")

  def legend(self, frame=None):
    """The drawing's key, each entry (kind of mark, color, words): the labels and marks the lane drew in frame (all of them without
    one), each fill as it shows over the relief, and, for a whole lane plan, the regions' access outlines, each kind of problem with
    its numbers, and which way the plan's axes run."""
    if frame is None:
      labels, flags, ledges = set(planFillLabels), trapFlag | layeredFlag | unreachedFlag | squeezeLayeredFlag, True
      edges, boxes = [(None, None, absorbed) for absorbed in (False, True)], [(None, None, reached) for reached in (True, False)]
      seeds, plots, accesses = [True, False], [True, False], {region["access"] for region in self.regions}
    else:
      label, flag, ledge = self.pixels(frame, frame.size)
      labels, flags, ledges = set(numpy.unique(label).tolist()), int(numpy.bitwise_or.reduce(flag, axis=None)), bool(ledge.any())
      edges = [edge for edge in self.edges(frame) if self.inFrame(frame, edge[0]) or self.inFrame(frame, edge[1])]
      boxes = [entry for entry in self.zoneLineBoxes(frame) if entry[0][2] > 0 and entry[0][3] > 0 and entry[0][0] < frame.size[0] and entry[0][1] < frame.size[1]]
      seeds = [seed["isolated"] for seed, entry in zip(self.description["seeds"], self.analysis["entries"]) if self.inFrame(frame, entry["at"])]
      plots = [plot["reached"] for plot in self.analysis["plotEntrances"] if self.inFrame(frame, plot["at"])]
      accesses = {region["access"] for region in self.regions if planDrawing.clippedToFrame([frame.pixel(point) for point in region["outline"]], frame.size)}
    marks = [
      (playerReach.walkLabel in labels, ("fill", overRelief(labelColors[playerReach.walkLabel], fillAlpha), "walk")),
      (playerReach.swimLabel in labels, ("fill", overRelief(labelColors[playerReach.swimLabel], fillAlpha), "swim")),
      (playerReach.slideLabel in labels, ("fill", overRelief(labelColors[playerReach.slideLabel], fillAlpha), "slide (too steep to stand)")),
      (playerReach.squeezeLabel in labels, ("fill", overRelief(labelColors[playerReach.squeezeLabel], fillAlpha), "squeeze (only a smaller player)")),
      (bool(flags & trapFlag), ("hatch", trapColor, "trap (no way back)")),
      (bool(flags & unreachedFlag), ("crossHatch", unreachedColor, "walkable, never reached")),
      (bool(flags & layeredFlag), ("dots", layeredColor, "reached under a higher level")),
      (bool(flags & squeezeLayeredFlag), ("dots", squeezeLayeredColor, "only a smaller player under it")),
      (ledges, ("ledge", ledgeColor, f"ledge: floors side by side over a step ({playerScale.stepHeight:g}) apart")),
      (any(not absorbed for _, _, absorbed in edges), ("line", voidColor, "leak edge (onto nothing or under the underworld)")),
      (any(absorbed for _, _, absorbed in edges), ("line", catchColor, "a zone line catches the fall")),
      (any(reached for _, _, reached in boxes), ("box", zoneLineColor, "zone line reached")),
      (any(not reached for _, _, reached in boxes), ("dashedBox", zoneLineColor, "zone line not reached")),
      (False in seeds, ("arrow", seedOutline, "seed: where players arrive")), (True in seeds, ("hollowArrow", seedOutline, "isolated seed")),
      (True in plots, ("plus", plotColor, "plot entrance reached")), (False in plots, ("cross", unreachedPlotColor, "plot entrance not reached")),
    ]
    items = [item for shown, item in marks if shown]
    if self.complete:
      items += [("dashed", regionColors[access], f"{access or 'undecided'} region") for access in ("play", "view", "none", None) if access in accesses]
      byKind = {}
      for problem in (self.analysis["problems"] if frame is None else self.problemsIn(frame)):
        byKind.setdefault(problem["kind"], []).append(problem["number"])
      items += [("ring", problemColor, f"{numberRuns(numbers)} {kind}") for kind, numbers in byKind.items()]
    return items + [("text", legendTextColor, "plan: +X (north) up, +Y left")]


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


reliefMiddle = tuple((low + high) / 2 for low, high in zip(reliefLow, reliefHigh))


def overRelief(color, alpha):
  """A color laid at alpha over the relief's middle grey, as a fill shows on the plan."""
  return tuple(round(channel * alpha / 255 + middle * (1 - alpha / 255)) for channel, middle in zip(color, reliefMiddle))


def ringRadius(problem):
  """A problem's ring: wide enough to go round the mark it lies on (a seed's arrow, a plot entrance's cross)."""
  return ringRadii.get(problem["kind"], problemRing)


def hatchBox(draw, box, color, spacing):
  """Diagonal lines spacing apart across a box (left, top, right, bottom) in pixels."""
  left, top, right, bottom = box
  for offset in range(math.floor(left - bottom), math.ceil(right - top) + 1, spacing):
    first, last = max(top, left - offset), min(bottom, right - offset)
    if last > first:
      draw.line([(first + offset, first), (last + offset, last)], fill=color, width=1)


def nameBeside(board, at, reach, text, color, size):
  """Write a mark's name reach pixels from it, right, left, under, or over it, the first clear of the labels and avoided boxes; right of
  it, moved the least it can, where none is."""
  x, y = at
  spots = (((x + reach + nameGap, y), "lm"), ((x - reach - nameGap, y), "rm"), ((x, y + reach + nameGap), "ma"), ((x, y - reach - nameGap), "mb"))
  for spot, anchor in spots:
    if board.isClear(board.textBox(spot, text, size, anchor)):
      board.write(spot, text, color, size, anchor)
      return
  board.place(spots[0][0], text, color, size, "lm")


def drawLegendItem(draw, left, top, item):
  """One legend entry at (left, top): its mark in a swatch, then its words."""
  kind, color, text = item
  right, bottom = left + legendSwatch, top + legendSwatch
  middle = (top + bottom) / 2
  if kind == "fill":
    draw.rectangle([left, top, right, bottom], fill=(*color, 255), outline=serverMapDrawing.darker(color))
  elif kind in ("hatch", "crossHatch"):
    draw.rectangle([left, top, right, bottom], fill=overRelief(color, unreachedFillAlpha) if kind == "crossHatch" else None, outline=(170, 170, 170))
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
  elif kind == "ledge":
    draw.line([(left, middle), (right, middle)], fill=color, width=2)
  elif kind == "dashed":
    planDrawing.dashedLine(draw, [(left, middle), (right, middle)], color, 3)
  elif kind == "box":
    draw.rectangle([left + 2, top + 2, right - 2, bottom - 2], fill=overRelief(color, zoneLineFillAlpha))
    hatchBox(draw, (left + 2, top + 2, right - 2, bottom - 2), color, zoneLineHatchSpacing)
    draw.rectangle([left + 2, top + 2, right - 2, bottom - 2], outline=color, width=3)
  elif kind == "dashedBox":
    corners = [(left + 2, top + 2), (right - 2, top + 2), (right - 2, bottom - 2), (left + 2, bottom - 2)]
    for start, end in zip(corners, corners[1:] + corners[:1]):
      draw.line([start, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)], fill=color, width=3)
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


def legendUnder(path, lanePlan, frame, planItems=()):
  """Add the legend of the plan drawn at path in frame as a band under it: planItems, what the plan draws under the lane
  (overlayLegend), then the lane's (LanePlan.legend)."""
  with Image.open(path) as opened:
    plan = opened.convert("RGB")
  withLegend(plan, list(planItems) + lanePlan.legend(frame)).save(path)


def overlayLegend(overlays):
  """Legend entries for what planDrawing.drawPlan draws of overlays under a lane: boundaries' walls and their lids and floors, water,
  and swim volumes, each only where the zone holds one."""
  items = []
  if any(boundary["lines"] for boundary in overlays["boundaries"]):
    items.append(("line", planDrawing.boundaryColor, "boundary wall"))
  if any(boundary["areas"] for boundary in overlays["boundaries"]):
    items.append(("fill", overRelief(planDrawing.boundaryColor, 70), "boundary lid or floor"))
  if overlays["water"]:
    items.append(("fill", overRelief(planDrawing.waterColor, 110), "water"))
  if overlays["swim"]:
    items.append(("dashedBox", planDrawing.swimColors["water"], "swim volume"))
  return items


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
