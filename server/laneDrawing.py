"""The player's lane in plan: where players reach, by label (walk green, swim blue, slide orange, squeeze violet, traps hatched orange),
walkable floor they never reach hatched grey, columns reached at a level under a higher one dotted, void edges red, zone lines filled
green when reached and dashed when not, seeds as white arrows (hollow when isolated), plot entrances as ticks (a red X when unreached),
region outlines in their access colors, and the problems numbered. LanePlan lays it over planDrawing.drawPlan's plan; drawLaneBesideNav
draws a lane from server files over their collision beside the server's NPC nav."""

import numpy
from PIL import Image, ImageDraw

import planDrawing
import playerReach
import serverMapDrawing

labelColors = {
  playerReach.walkLabel: (35, 165, 60), playerReach.swimLabel: (25, 105, 225), playerReach.slideLabel: (240, 140, 20),
  playerReach.squeezeLabel: (150, 65, 205), playerReach.zoneLineLabel: (20, 140, 40),
}
fillAlpha = 125
hatchAlpha = 215
hatchSpacing = 8
trapColor = (240, 120, 0)
unreachedColor = (120, 120, 120)
layeredColor = (25, 25, 25)
layeredSpacing = 6
voidColor = (225, 20, 20)
voidWidth = 3
zoneLineColor = planDrawing.zoneLineColor
seedFill = (255, 255, 255)
seedOutline = (20, 20, 20)
seedLength = 20
seedWidth = 18
plotColor = planDrawing.plotColor
unreachedPlotColor = (220, 20, 20)
plotMark = 7
problemColor = (190, 0, 0)
problemRing = 9
regionColors = {"play": planDrawing.regionColor, "view": planDrawing.accessColors["view"], "none": planDrawing.accessColors["none"], None: planDrawing.accessColors[None]}
legendRowHeight = 22
chunkNodes = 1 << 22


class LanePlan:
  """A lane drawn over a plan: fill (the labels, hatches, and dots as one layer), strokes (void edges, zone lines, seeds, plot entrances,
  region outlines, problem rings), and labels (problem numbers, seed and region names). level {bottom, top} keeps only nodes whose height
  lies in it; numbered false leaves the problems out."""

  def __init__(self, analysis, description, level=None, numbered=True):
    nodes = analysis["nodes"]
    self.lane = lane = nodes.lane
    self.analysis, self.description, self.numbered = analysis, description, numbered
    inBand = numpy.ones(lane.nodeCount, dtype=bool) if level is None else (nodes.height >= level["bottom"]) & (nodes.height <= level["top"])
    self.label = numpy.zeros(lane.columns, dtype=numpy.uint8)
    self.flags = numpy.zeros(lane.columns, dtype=numpy.uint8)
    layered = numpy.zeros(lane.columns, dtype=bool)
    unreached = numpy.zeros(lane.columns, dtype=bool)
    lastFloor = -1
    # A column's nodes lie together, lowest first, so the last reached one of a run is the column's top; a column split between two
    # chunks has its higher nodes in the later one, which writes over the earlier.
    for start in range(0, lane.nodeCount, chunkNodes):
      part = slice(start, min(lane.nodeCount, start + chunkNodes))
      flat = nodes.z[part].astype(numpy.int32) * lane.columnsX + nodes.x[part]
      band = inBand[part]
      reached = numpy.flatnonzero(nodes.reached[part] & band)
      if len(reached):
        columns = flat[reached]
        top = numpy.append(columns[1:] != columns[:-1], True)
        self.label[columns[top]] = nodes.label[part][reached[top]]
        self.flags[columns[top]] = numpy.where(nodes.trap[part][reached[top]], 1, 0).astype(numpy.uint8)
        floors = columns[~nodes.wet[part][reached]]
        if len(floors):
          layered[floors[1:][floors[1:] == floors[:-1]]] = True
          layered[floors[0]] |= floors[0] == lastFloor
          lastFloor = floors[-1]
      walkable = nodes.walkable[part] & ~nodes.wet[part] & ((nodes.shape[part] & playerReach.absorbedShape) == 0) & band
      unreached[flat[walkable]] = True
    self.flags[layered] |= 2
    del layered
    unreached[self.label > 0] = False
    self.flags[unreached] |= 4
    self.label = self.label.reshape(lane.columnsZ, lane.columnsX)
    self.flags = self.flags.reshape(lane.columnsZ, lane.columnsX)
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
    trapHatch = ((across + down) % hatchSpacing < 2) & ((flags & 1) != 0)
    pixels[trapHatch] = (*trapColor, hatchAlpha)
    unreachedHatch = ((across - down) % hatchSpacing < 2) & ((flags & 4) != 0)
    pixels[unreachedHatch] = (*unreachedColor, hatchAlpha)
    dots = (across % layeredSpacing == 0) & (down % layeredSpacing == 0) & ((flags & 2) != 0)
    pixels[dots] = (*layeredColor, 255)
    return Image.fromarray(pixels, "RGBA")

  def strokes(self, draw, frame):
    lane = self.lane
    for region in self.description["regions"]:
      points = [frame.pixel(point) for point in region["outline"]]
      planDrawing.dashedLine(draw, points + points[:1], (*regionColors[region["access"]], 255), 3)
    if self.fromColumns is not None:
      for (fromX, fromZ), event in zip(zip(*self.fromColumns), lane.events):
        if event[0] == playerReach.absorbedEvent:
          continue
        x, z = lane.columnPlaces(numpy.array([event[2]], dtype=numpy.int64))
        start, end = edgeBetween(lane, (int(fromX), int(fromZ)), (int(x[0]), int(z[0])))
        draw.line([frame.pixel(start), frame.pixel(end)], fill=(*voidColor, 255), width=voidWidth)
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
    if self.numbered:
      for problem in self.analysis["problems"]:
        x, y = frame.pixel(problem["center"])
        draw.ellipse([x - problemRing, y - problemRing, x + problemRing, y + problemRing], outline=(*problemColor, 255), width=3)

  def labels(self, board, frame):
    if self.numbered:
      for problem in self.analysis["problems"]:
        x, y = frame.pixel(problem["center"])
        board.place((x + problemRing + 2, y - problemRing - 2), str(problem["number"]), problemColor, 17, "lb")
    for entry in self.analysis["entries"]:
      x, y = frame.pixel(entry["at"])
      board.place((x + seedLength, y), "S" if entry["kind"] == "safePoint" else entry["name"], seedOutline, 13, "lm")
    for region in self.description["regions"]:
      board.place(planDrawing.centroidOf([frame.pixel(point) for point in region["outline"]]), region["name"], regionColors[region["access"]], 13)

  def legend(self):
    """The drawing's key: each color and what it marks."""
    return [
      (labelColors[playerReach.walkLabel], "walk"), (labelColors[playerReach.swimLabel], "swim"), (labelColors[playerReach.slideLabel], "slide"),
      (labelColors[playerReach.squeezeLabel], "squeeze"), (trapColor, "trap (hatched)"), (unreachedColor, "walkable, unreached (hatched)"),
      (voidColor, "void edge"), (zoneLineColor, "zone line: filled reached, dashed not"),
    ]


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


def lanePanel(frame, base, lanePlan):
  """A lane over a base image in a frame, with the grid, scale and north: the image and its label board."""
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


def drawLaneBesideNav(outputPath, serverTriangles, lanePlan, inspection, titles, panelWidth=900):
  """A lane from a zone's server files over their collision (server axes) and, on the same frame, the server's NPC nav by component
  (serverMapDrawing.componentPanel of inspection), side by side, each titled and with its legend. Written as PNG; returns the sheet's
  size, the frame, and the legends."""
  frame = serverMapDrawing.collisionFrame(serverTriangles, panelWidth)
  base = serverMapDrawing.shadedCollision(numpy.asarray(serverTriangles)[..., [1, 0, 2]], frame)
  left = lanePanel(frame, base, lanePlan)
  navPanel = serverMapDrawing.componentPanel(titles[1], inspection)
  right, _ = serverMapDrawing.drawNavPanel(navPanel, frame)
  legends = [lanePlan.legend(), navPanel["legend"]]
  legendHeight = legendRowHeight * max(len(legend) for legend in legends) + serverMapDrawing.panelGap
  gap = serverMapDrawing.panelGap
  sheet = Image.new("RGB", (2 * frame.size[0] + 3 * gap, serverMapDrawing.titleHeight + frame.size[1] + legendHeight + gap), (255, 255, 255))
  draw = ImageDraw.Draw(sheet)
  for index, (panel, title, legend) in enumerate(zip((left, right), titles, legends)):
    corner = gap + index * (frame.size[0] + gap)
    sheet.paste(panel.convert("RGB"), (corner, serverMapDrawing.titleHeight))
    draw.text((corner, gap), title, fill=serverMapDrawing.titleColor, font=planDrawing.fontOf(17))
    for row, (color, text) in enumerate(legend):
      top = serverMapDrawing.titleHeight + frame.size[1] + 6 + row * legendRowHeight
      size = serverMapDrawing.swatchSize
      draw.rectangle([corner, top, corner + size, top + size], fill=color, outline=serverMapDrawing.darker(color))
      draw.text((corner + size + 8, top - 1), text, fill=serverMapDrawing.titleColor, font=planDrawing.fontOf(15))
  sheet.save(outputPath, "PNG")
  return {"width": sheet.width, "height": sheet.height, "frame": {"center": list(frame.center), "width": frame.width, "size": list(frame.size)}, "legends": [[text for _, text in legend] for legend in legends]}
