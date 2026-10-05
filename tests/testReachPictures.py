import base64
import io
import json
import sys
from pathlib import Path

import numpy
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import laneDrawing
import planDrawing
from testReach import reachCourse


def pixelsAround(image, plan, point, radius=4):
  """The drawing's pixels within radius of where a zone point falls in a plan ({center, width}), as an [n, 3] array."""
  frame = planDrawing.PlanFrame(plan["center"], plan["width"], image.size)
  x, y = (round(value) for value in frame.pixel(point))
  return numpy.asarray(image.convert("RGB"))[y - radius:y + radius + 1, x - radius:x + radius + 1].reshape(-1, 3).astype(int)


def greenShare(pixels):
  """The share of pixels tinted the lane's walk green: green well over red and blue."""
  return float(((pixels[:, 1] > pixels[:, 0] + 30) & (pixels[:, 1] > pixels[:, 2] + 20)).mean())


def hatchShare(pixels, color):
  return float((numpy.abs(pixels - numpy.array(color)).max(axis=1) <= 40).mean())


def testMapReachDrawsTheLaneAndItsProblems(stageBlenderServer, tmp_path):
  async def steps(session):
    await reachCourse(session, tmp_path)
    whole, _ = await session.call("mapReach", {})
    framed = await session.expectImage("mapReach", {"center": [60, 40], "width": 240, "problemViews": False})
    leveled = await session.expectImage("mapReach", {"center": [160, 40], "width": 80, "level": {"bottom": 9, "top": 11}, "problemViews": False})
    sketched = await session.expectImage("renderSketch", {"center": [60, 40], "width": 240, "layers": ["regions", "lane"]})
    plain = await session.expectImage("renderSketch", {"center": [60, 40], "width": 240, "layers": ["regions"]})
    return whole, framed, leveled, sketched, plain

  whole, framed, leveled, sketched, plain = stageBlenderServer.session(steps)
  assert whole.is_error is False, whole.content[0].text
  assert [content.type for content in whole.content] == ["image", "image", "text"]
  assert [content.mime_type for content in whole.content[:2]] == ["image/png", "image/jpeg"]
  report = json.loads(whole.content[2].text)
  (tmp_path / "coursePlan.png").write_bytes(base64.b64decode(whole.content[0].data))
  (tmp_path / "courseProblems.jpg").write_bytes(base64.b64decode(whole.content[1].data))
  # The problem sheet: a view at eye height from the reached floor nearest each of the first eight problems, facing it, and the plan.
  sheet = report["problemSheet"]
  assert [view["number"] for view in sheet["views"]] == list(range(1, 9)) and all(view["view"] is not None for view in sheet["views"])
  assert all(set(view["view"]) == {"standOn", "headingDegrees", "pitchDegrees"} for view in sheet["views"])
  assert Image.open(io.BytesIO(base64.b64decode(whole.content[1].data))).size == (sheet["width"], sheet["height"]) and sheet["width"] == 8 + 3 * (640 + 8)

  planImage, planReport = framed
  plan = Image.open(io.BytesIO(planImage))
  plan.save(tmp_path / "courseStations.png")
  frame = {"center": planReport["plan"]["center"], "width": planReport["plan"]["width"]}
  # Walked ground and the platform the gentle ramp reaches are green; the tops of the step over a player's step and of the platform
  # past the steep ramp are hatched grey, walkable floor never reached, and ringed red as problems.
  assert greenShare(pixelsAround(plan, frame, [20, -10])) > 0.9
  assert greenShare(pixelsAround(plan, frame, [80, 45])) > 0.9
  for point in ([40, 70], [110, 43]):
    around = pixelsAround(plan, frame, point)
    assert greenShare(around) == 0 and hatchShare(around, laneDrawing.unreachedColor) > 0.1
  stepOver = next(problem for problem in planReport["problems"] if problem["kind"] == "unreachedPlayFloor" and abs(problem["center"][1] - 70) < 3)
  assert hatchShare(pixelsAround(plan, frame, stepOver["center"], 12), laneDrawing.problemColor) > 0.02

  # Kept to the band from 9 to 11, the plan draws the ledge's top and not the ground beside it.
  levelImage, levelReport = leveled
  levelPlan = Image.open(io.BytesIO(levelImage))
  levelFrame = {"center": levelReport["plan"]["center"], "width": levelReport["plan"]["width"]}
  assert levelReport["plan"]["level"] == {"bottom": 9, "top": 11}
  assert greenShare(pixelsAround(levelPlan, levelFrame, [160, 36])) > 0.9
  assert greenShare(pixelsAround(levelPlan, levelFrame, [185, 36])) == 0

  # renderSketch's lane layer lays the same fill over its plan, and only when asked for.
  sketchImage, sketchReport = sketched
  plainImage, plainReport = plain
  sketch, bare = Image.open(io.BytesIO(sketchImage)), Image.open(io.BytesIO(plainImage))
  sketch.save(tmp_path / "courseSketchLane.png")
  sketchFrame = {"center": sketchReport["center"], "width": sketchReport["width"]}
  assert sketchReport["laneState"]["lane"] == "reused" and "laneState" not in plainReport
  assert greenShare(pixelsAround(sketch, sketchFrame, [20, -10])) > 0.9
  assert greenShare(pixelsAround(bare, sketchFrame, [20, -10])) == 0
