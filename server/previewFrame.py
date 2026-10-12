"""The frame a preview renders at when its view sets none: the RoF2 client's camera at 16:9. Read by the bridge, which renders it, and
by the server, which aims views and marks what they show."""
import math

renderWidth = 960
renderHeight = 540
anglesPerTurn = 512
# RoF2's camera (CCamera +0x04, read in claudelink's dumps as halfViewAngle): half the horizontal view is 45/512 of a turn.
clientHalfViewAngle = 45.0


def verticalFieldOfView(halfViewAngle, aspect):
  """The client's vertical field of view in degrees: its projection scales x by the cotangent of the half view angle (512ths of a
  turn) and y by that times the view's aspect (viewAngleRatio, width over height)."""
  return math.degrees(2 * math.atan(math.tan(halfViewAngle * 2 * math.pi / anglesPerTurn) / aspect))


verticalFieldOfViewDegrees = verticalFieldOfView(clientHalfViewAngle, renderWidth / renderHeight)
