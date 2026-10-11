"""The frame a preview renders at when its view sets none: the live client's first-person view. Read by the bridge, which renders it,
and by the server, which aims views and marks what they show."""
renderWidth = 960
renderHeight = 540
# Measured by aligning renders to Plane of Knowledge and Eastern Wastes screenshots (16:9 crops of the live client's first-person view).
verticalFieldOfViewDegrees = 46.5
