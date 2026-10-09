"""導入(amig provision): 台帳から入れる・確かめる・固定する。

実機を触らずに確かめるため、外部コマンドは Runner を差し替えて記録だけ取る。
取得を伴う経路は dry-run と既存ファイルの照合で確かめる。
"""

import hashlib
from pathlib import Path

import pytest
import yaml

from amig import provision as prov

MANIFEST = {
    "apt": ["postgresql", "podman"],
    "venv": "/opt/amig/venv",
    "python": ["fastapi", "cf-publish>=0.2"],
    "binaries": [
        {
            "name": "pocketbase",
            "url": "https://example.invalid/pocketbase.zip",
            "dest": "",  # テストごとに差し替える
            "sha256": "",
            "service": {"exec": "/opt/pb/pocketbase serve", "user": "pocketbase"},
        }
    ],
    "containers": [
        {
            "name": "onlyoffice",
            "image": "docker.io/onlyoffice/documentserver",
            "digest": "sha256:aaa",
            "ports": ["127.0.0.1:8081:80"],
        }
    ],
    "compose": [
        {
            "name": "jitsi",
            "url": "https://example.invalid/docker-compose.yml",
            "dir": "",
            "sha256": "",
        }
    ],
}


class FakeRunner(prov.Runner):
    """成否を指定できる Runner(実機を触らない)。"""

    def __init__(self, ok_map: dict[str, bool] | None = None) -> None:
        super().__init__(dry_run=True)
        self.ok_map = ok_map or {}

    def ok(self, cmd: list[str]) -> bool:
        self.log.append(list(cmd))
        for key, val in self.ok_map.items():
            if key in " ".join(cmd):
                return val
        return True


def _write(tmp_path: Path, **over) -> prov.Manifest:
    cfg = yaml.safe_load(yaml.safe_dump(MANIFEST))  # 深いコピー
    cfg["binaries"][0]["dest"] = str(tmp_path / "opt" / "pocketbase")
    cfg["compose"][0]["dir"] = str(tmp_path / "jitsi")
    for k, v in over.items():
        cfg[k] = v
    path = tmp_path / "provision.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return prov.load(path)


def test_load_validates(tmp_path):
    m = _write(tmp_path)
    assert m.apt == ("postgresql", "podman")
    assert m.binaries[0].name == "pocketbase"
    assert m.containers[0].digest == "sha256:aaa"
    assert m.compose[0].name == "jitsi"

    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"binaries": [{"name": "x"}]}), encoding="utf-8")
    with pytest.raises(prov.ProvisionError, match="url"):
        prov.load(bad)


def test_apply_dry_run_calls(tmp_path):
    """dry-run では実行せず、打つ手だけが並ぶ。"""
    m = _write(tmp_path)
    m = prov.load(m.path)
    # sha256 を入れておく(未固定は入れない仕様なので別テストで確かめる)
    cfg = yaml.safe_load(m.path.read_text(encoding="utf-8"))
    cfg["binaries"][0]["sha256"] = "0" * 64
    cfg["compose"][0]["sha256"] = "1" * 64
    m.path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    m = prov.load(m.path)

    r = prov.Runner(dry_run=True)
    results = prov.Provisioner(m, r).apply()
    cmds = [" ".join(c) for c in r.log]
    assert any("apt-get install" in c for c in cmds)
    assert any("python3 -m venv" in c for c in cmds)
    assert any("systemctl enable --now pocketbase.service" in c for c in cmds)
    assert any(
        "podman pull docker.io/onlyoffice/documentserver@sha256:aaa" in c for c in cmds
    )
    assert any("podman-compose" in c for c in cmds)
    assert all(x.ok for x in results)


def test_refuse_unpinned(tmp_path):
    """固定していない物は入れない(黙って違う物が入らない)。"""
    m = _write(tmp_path)
    with pytest.raises(prov.ProvisionError, match="sha256"):
        prov.Provisioner(m, prov.Runner(dry_run=True)).apply()


def test_binary_hash_mismatch_stops(tmp_path):
    """既にある実物が台帳と違えば、照合で落ちる。"""
    dest = tmp_path / "opt" / "pocketbase"
    dest.parent.mkdir(parents=True)
    dest.write_bytes("ちがう中身".encode())
    actual = hashlib.sha256("ちがう中身".encode()).hexdigest()

    cfg = yaml.safe_load(yaml.safe_dump(MANIFEST))
    cfg["binaries"][0]["dest"] = str(dest)
    cfg["binaries"][0]["sha256"] = "9" * 64
    cfg["compose"] = []
    cfg["containers"] = []
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    m = prov.load(path)

    results = prov.Provisioner(m, FakeRunner()).check()
    binres = [r for r in results if r.kind == "binary"][0]
    assert not binres.ok
    assert actual in binres.detail  # 実物のハッシュを成績書に残す


def test_check_reports_missing(tmp_path):
    m = _write(tmp_path)
    runner = FakeRunner({"dpkg -s postgresql": False, "is-active": False})
    results = prov.Provisioner(m, runner).check()
    apt = {r.name: r.ok for r in results if r.kind == "apt"}
    assert apt["postgresql"] is False and apt["podman"] is True
    svc = [r for r in results if r.kind == "service"][0]
    assert not svc.ok
    # ファイルが無い・compose 定義が無いことも落ちる
    assert not [r for r in results if r.kind == "binary"][0].ok
    assert not [r for r in results if r.kind == "compose"][0].ok


def test_python_dist_name():
    assert prov._dist_name("cf-publish>=0.2") == "cf-publish"
    assert prov._dist_name("uvicorn[standard]") == "uvicorn"


def test_receipt_is_a_certificate(tmp_path):
    """成績書は判定と内容を持ち、不合格が分かる形で残る。"""
    m = _write(tmp_path)
    results = [
        prov.Result("apt", "postgresql", True, "導入済み"),
        prov.Result("service", "forgejo", False, "**停止**"),
    ]
    text = prov.receipt(m, results, "照合(--check)")
    assert "受け入れ検査 成績書" in text
    assert "不合格" in text and "1/2 項目" in text
    assert "| service | forgejo | × | **停止** |" in text
    assert "施工と検査は別の仕事" in text


def test_pin_records_into_manifest(tmp_path, monkeypatch):
    """--pin は実測値を台帳に書き込む(先に表、後に線)。"""
    m = _write(tmp_path)
    payload = b"pocketbase-binary"
    want = hashlib.sha256(payload).hexdigest()

    def fake_download(url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload if "pocketbase" in url else b"compose-yaml")
        return dest

    monkeypatch.setattr(prov, "download", fake_download)
    runner = prov.Runner(dry_run=False)
    runner.run = lambda cmd, check=True: type(  # type: ignore[method-assign]
        "R", (), {"stdout": '"sha256:bbb"', "returncode": 0}
    )()
    prov.Provisioner(m, runner).pin()

    cfg = yaml.safe_load(m.path.read_text(encoding="utf-8"))
    assert cfg["binaries"][0]["sha256"] == want
    assert cfg["containers"][0]["digest"] == "sha256:aaa"  # 固定済みは触らない
    assert cfg["compose"][0]["sha256"] == hashlib.sha256(b"compose-yaml").hexdigest()


def test_shipped_manifest_is_valid():
    """同梱の台帳が読めること(版は未固定でよい)。"""
    root = Path(__file__).parent.parent
    m = prov.load(root / "provision.yaml")
    names = {b.name for b in m.binaries}
    assert {"pocketbase", "forgejo", "stalwart"} <= names
    assert "postgresql" in m.apt
    # 大きなアプリは載せない(会議・予約・文書サーバー・問い合わせの窓口は
    # 共有基盤の上に薄く作る)。検査が届く大きさに保つ——§19
    assert m.containers == () and m.compose == ()
    assert "podman" not in m.apt
    # ローカルAIは入れない(§19)
    text = (root / "provision.yaml").read_text(encoding="utf-8")
    assert "ollama" not in text.lower()


class StdoutRunner(prov.Runner):
    """stdout を返せる Runner(所属グループの照合に使う)。"""

    def __init__(self, stdout: str = "") -> None:
        super().__init__(dry_run=True)
        self.stdout = stdout

    def run(self, cmd, check=True):
        self.log.append(list(cmd))
        return type("R", (), {"stdout": self.stdout, "returncode": 0})()


def test_groups_apply_and_check(tmp_path):
    """使う人を機器の操作グループに入れる(入っていなければ不合格)。"""
    cfg = {"groups": [{"user": "engineer", "add": ["dialout", "plugdev"]}]}
    path = tmp_path / "g.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    m = prov.load(path)

    r = prov.Runner(dry_run=True)
    prov.Provisioner(m, r).apply()
    assert ["usermod", "-aG", "dialout,plugdev", "engineer"] in r.log

    ok = prov.Provisioner(m, StdoutRunner("engineer dialout plugdev")).check()
    assert ok[0].ok
    ng = prov.Provisioner(m, StdoutRunner("engineer dialout")).check()
    assert not ng[0].ok and "plugdev" in ng[0].detail


def test_engineer_manifest():
    """設計・組込み向けの台帳: CAD/EDA/組込みが入り、重い常駐は入らない。"""
    root = Path(__file__).parent.parent
    m = prov.load(root / "provision-engineer.yaml")
    assert {"freecad", "kicad", "openscad", "librecad", "solvespace"} <= set(m.apt)
    assert {"ngspice", "inkscape", "meshlab", "admesh", "blender"} <= set(m.apt)
    # 解析(構造・熱): 性能を数字で確かめる
    assert {"calculix-ccx", "gmsh", "paraview"} <= set(m.apt)
    # Python で書くパラメトリック CAD(AI に部品を書かせられる)
    assert {"cadquery", "build123d"} <= set(m.python)
    assert {"openocd", "avrdude", "gcc-arm-none-eabi", "picocom"} <= set(m.apt)
    assert "platformio" in m.python and "esptool" in m.python
    assert m.binaries[0].name == "arduino-cli"
    # 重い常駐サービスは外す(要らないものを入れない)
    assert m.containers == () and m.compose == ()
    # 書き込み権限が台帳にある(無いと実機に書けない箱になる)
    users = dict(m.groups)
    assert "dialout" in users["engineer"] and "plugdev" in users["engineer"]
    # ローカルAIはここにも入れない(§19)
    text = (root / "provision-engineer.yaml").read_text(encoding="utf-8")
    assert "ollama" not in text.lower()


def test_deb_apply_check_pin(tmp_path, monkeypatch):
    """配布物(.deb)は固定して取得し、依存解決は apt に任せる。"""
    cfg = {
        "deb": [
            {
                "name": "euro-office-docs",
                "url": "https://x.invalid/e.deb",
                "package": "euro-office-documentserver",
                "sha256": "",
            }
        ]
    }
    path = tmp_path / "d.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    # 固定していなければ入れない
    with pytest.raises(prov.ProvisionError, match="sha256"):
        prov.Provisioner(prov.load(path), prov.Runner(dry_run=True)).apply()

    # --pin で実測値が台帳に入る
    payload = b"deb-bytes"
    monkeypatch.setattr(
        prov,
        "download",
        lambda url, dest: (
            dest.parent.mkdir(parents=True, exist_ok=True),
            dest.write_bytes(payload),
            dest,
        )[-1],
    )
    prov.Provisioner(prov.load(path), prov.Runner(dry_run=False)).pin()
    got = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert got["deb"][0]["sha256"] == hashlib.sha256(payload).hexdigest()

    # 導入は apt に渡す(依存解決を自作しない)
    r = prov.Runner(dry_run=True)
    prov.Provisioner(prov.load(path), r).apply()
    assert any(c[:3] == ["apt-get", "install", "-y"] for c in r.log)

    # 照合は dpkg 上の名前で見る(台帳の name とは別でよい)
    runner = FakeRunner({"euro-office-documentserver": True})
    res = prov.Provisioner(prov.load(path), runner).check()
    assert res[0].kind == "deb" and res[0].ok


def test_document_engine_is_borrowed_server_side():
    """文書エンジンはサーバー版を一台目に置き、エンジニア機には置かない。"""
    root = Path(__file__).parent.parent
    base = prov.load(root / "provision.yaml")
    eng = prov.load(root / "provision-engineer.yaml")
    assert [d.package for d in base.debs] == ["euro-office-documentserver"]
    assert eng.debs == ()  # 文書サーバーは組織に一台。設計者の机に常駐させない
    assert base.containers == ()  # 借りるのはエンジンだけ(丸ごとの platform は載せない)
