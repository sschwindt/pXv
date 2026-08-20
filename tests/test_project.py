from pxv.project import Project, load_project, save_project
import pytest


def test_roundtrip(tmp_path):
    p = Project(name="demo")
    p.piv.windows = [96, 48]
    p.video.roi = [1, 2, 3, 4]
    path = tmp_path / "p.yaml"
    save_project(p, path)
    q = load_project(path)
    assert q.name == "demo"
    assert q.piv.windows == [96, 48]
    assert q.video.roi == [1, 2, 3, 4]


def test_dotted_access():
    p = Project()
    p.set("piv.corr_threshold", 0.42)
    assert p.get("piv.corr_threshold") == 0.42


def test_unknown_key_rejected():
    with pytest.raises(ValueError, match="unknown keys"):
        Project.from_dict({"piv": {"nonsense": 1}})


def test_unknown_section_rejected():
    with pytest.raises(ValueError, match="unknown project section"):
        Project.from_dict({"nope": {}})
