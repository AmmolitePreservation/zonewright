"""A client model's views for the asset catalog: front (looking at its +X front), side (from its +Y side), top, and three-quarter, each
drawn as the client draws it in the neutral daylight of bridgeViews' model thumbnails and framed to fit. Runs under Blender's Python."""
import math
import os

import bpy
import mathutils

import bridgeModels
import bridgeViews

viewDirections = {
  "front": mathutils.Vector((1.0, 0.0, 0.0)),
  "side": mathutils.Vector((0.0, 1.0, 0.0)),
  "top": mathutils.Vector((0.0, -1e-4, 1.0)).normalized(),
  "threeQuarter": bridgeViews.thumbnailViewDirection,
}


def renderModelViews(models, views):
  """Each model ([{folder, outputFolder}]) drawn from each named view into <outputFolder>\\<view>.png; returns the files."""
  unknown = sorted(set(views) - set(viewDirections))
  if unknown or not views:
    raise ValueError(f"views are among {list(viewDirections)}; got {views}")
  scratch = bpy.data.scenes.new(bridgeViews.previewName + "ModelViews")
  written = []
  try:
    for index, model in enumerate(models):
      os.makedirs(model["outputFolder"], exist_ok=True)
      modelObject = bridgeModels.modelObject(model["folder"], f"{bridgeViews.previewName}ModelView{index}", 1, (0, 0, 0), 0)
      scratch.collection.objects.link(modelObject)
      files = {}
      try:
        for view in views:
          preview = bridgeViews.PreviewScene(scratch, bridgeViews.thumbnailZone)
          try:
            preview.scene.render.resolution_x = preview.scene.render.resolution_y = bridgeViews.thumbnailSide
            corners = [modelObject.matrix_world @ mathutils.Vector(corner) for corner in modelObject.evaluated_get(preview.depsgraph()).bound_box]
            center = sum(corners, mathutils.Vector()) / len(corners)
            radius = max(max((corner - center).length for corner in corners), 0.01)
            distance = radius / math.sin(math.radians(bridgeViews.verticalFieldOfViewDegrees / 2)) * 1.05
            direction = viewDirections[view]
            preview.camera.location = center + direction * distance
            preview.camera.rotation_mode = "QUATERNION"
            preview.camera.rotation_quaternion = bridgeViews.lookRotation(-direction)
            preview.camera.data.clip_start = distance / 100
            preview.camera.data.clip_end = distance + 2 * radius
            outputPath = os.path.join(model["outputFolder"], f"{view}.png")
            preview.scene.render.filepath = outputPath
            bpy.ops.render.render(write_still=True, scene=preview.scene.name)
            files[view] = outputPath
          finally:
            preview.remove()
      finally:
        mesh = modelObject.data
        bpy.data.objects.remove(modelObject)
        bpy.data.meshes.remove(mesh)
      written.append(files)
  finally:
    bpy.data.scenes.remove(scratch)
  return {"views": written}


commands = {"renderModelViews": (renderModelViews, False)}
