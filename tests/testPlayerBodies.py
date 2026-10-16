import sys
from pathlib import Path

import pytest

from conftest import everquestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqModels
import eqRaces
import playerScale

standPose = {"animation": None, "variant": None, "frame": 0}


@pytest.mark.clientData("clientFiles")
def testBodiesAreTheClientsModelsAtTheirSpawnHeights(tmp_path):
  clientRoot = Path(everquestClient)
  eqModels.loadIndex(clientRoot, tmp_path)
  for body in playerScale.bodies:
    _, details = eqModels.buildModel(clientRoot, tmp_path, body.code, None, None, None, standPose)
    scale = eqRaces.spawnScale(body.code, body.spawnHeight, False)
    low, high = details["minimum"], details["maximum"]
    measured = (round((high[2] - low[2]) * scale, 2), round((high[1] - low[1]) * scale, 2), round((high[0] - low[0]) * scale, 2))
    assert measured == (body.modelHeight, body.width, body.depth), body.code
    assert eqRaces.avatarHeight(clientRoot, body.code, scale) == pytest.approx(body.avatarHeight), body.code
    # Only the frogloks, whose stand no linked archive holds, stand in their bind pose.
    assert ("bind" in details["pose"]) == (body.code in playerScale.bindPoseCodes), body.code
