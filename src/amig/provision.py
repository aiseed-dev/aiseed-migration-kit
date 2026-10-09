"""導入: 台帳(provision.yaml)から開発用PC/一台目のサーバーを組み立てる。

§19 のベース一式を、手作業ではなく台帳から入れる。形は §16 の許可表と同じ
——**表が正、実機は表から作り、突き合わせて確かめる**:

    provision.yaml      何を入れるか(台帳。リポジトリで版を管理する)
      ↓ apply           台帳のとおりに入れる(冪等・再実行できる)
      ↓ check           入っている物と台帳を突き合わせる
    成績書              版とハッシュ付きの受け入れ検査結果(§19 の納品物)

設計の約束:

- **依存解決・TLS発行・コンテナ実行は自作しない。** apt / pip / podman を
  呼ぶだけにして、自作の面積を小さく保つ(§18「作る量の原則」)。ここが
  小さいから、検査が全数まで届く
- **取得物は必ず照合する。** バイナリは SHA-256、コンテナはダイジェストで
  固定する。固定していない物は入れない——`--pin` で一度だけ実測して台帳に
  書き込み、以後の導入は毎回その値と突き合わせる(先に表、後に線)
- **root で動く面積を小さく保つ。** 実行するのは台帳に書かれた既知の
  コマンドだけで、任意のシェルは走らせない
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DOWNLOAD_TIMEOUT = 300
UNIT_DIR = Path("/etc/systemd/system")


class ProvisionError(Exception):
    """台帳の不備・導入の失敗。メッセージは運用担当者向けの日本語。"""


# ---- 台帳 ----


@dataclass(frozen=True)
class Binary:
    """単一バイナリで動く部品(身元・版管理・メール等)。"""

    name: str
    url: str
    dest: str
    sha256: str = ""
    archive: str = ""  # "" | "zip" | "tar.gz"
    member: str = ""  # 書庫の中の取り出すファイル
    service: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Container:
    """コンテナで動かす部品(重い依存を持つもの)。"""

    name: str
    image: str
    digest: str = ""
    ports: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    volumes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Deb:
    """配布物(.deb)で入る机の道具。

    依存解決は apt に任せる(自作しない)。取得物はハッシュで固定する。
    """

    name: str
    url: str
    sha256: str = ""
    package: str = ""  # dpkg 上の名前(既定は name)

    @property
    def pkg(self) -> str:
        return self.package or self.name


@dataclass(frozen=True)
class Compose:
    """複数のコンテナで一組になる部品(会議基盤など)。

    上流が配る compose 定義を**書き直さずに使い**、ハッシュで固定する。
    自作の面積を広げないための型(§18「作る量の原則」)。
    """

    name: str
    url: str
    dir: str
    sha256: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Manifest:
    """台帳1つ。"""

    path: Path
    apt: tuple[str, ...] = ()
    python: tuple[str, ...] = ()
    venv: str = "/opt/amig/venv"
    binaries: tuple[Binary, ...] = ()
    debs: tuple[Deb, ...] = ()
    containers: tuple[Container, ...] = ()
    compose: tuple[Compose, ...] = ()
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()


def load(path: str | Path) -> Manifest:
    """provision.yaml を読む。"""
    path = Path(path)
    if not path.exists():
        raise ProvisionError(f"{path} がありません(台帳を指定してください)")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    bins = []
    for b in cfg.get("binaries") or []:
        for k in ("name", "url", "dest"):
            if not b.get(k):
                raise ProvisionError(f"binaries の {k} がありません: {b}")
        bins.append(
            Binary(
                name=str(b["name"]),
                url=str(b["url"]),
                dest=str(b["dest"]),
                sha256=str(b.get("sha256") or ""),
                archive=str(b.get("archive") or ""),
                member=str(b.get("member") or ""),
                service=dict(b.get("service") or {}),
            )
        )
    cons = []
    for c in cfg.get("containers") or []:
        for k in ("name", "image"):
            if not c.get(k):
                raise ProvisionError(f"containers の {k} がありません: {c}")
        cons.append(
            Container(
                name=str(c["name"]),
                image=str(c["image"]),
                digest=str(c.get("digest") or ""),
                ports=tuple(str(x) for x in c.get("ports") or ()),
                env={str(k): str(v) for k, v in (c.get("env") or {}).items()},
                volumes=tuple(str(x) for x in c.get("volumes") or ()),
            )
        )
    comps = []
    for c in cfg.get("compose") or []:
        for k in ("name", "url", "dir"):
            if not c.get(k):
                raise ProvisionError(f"compose の {k} がありません: {c}")
        comps.append(
            Compose(
                name=str(c["name"]),
                url=str(c["url"]),
                dir=str(c["dir"]),
                sha256=str(c.get("sha256") or ""),
                env={str(k): str(v) for k, v in (c.get("env") or {}).items()},
            )
        )
    debs = []
    for d in cfg.get("deb") or []:
        for k in ("name", "url"):
            if not d.get(k):
                raise ProvisionError(f"deb の {k} がありません: {d}")
        debs.append(
            Deb(
                name=str(d["name"]),
                url=str(d["url"]),
                sha256=str(d.get("sha256") or ""),
                package=str(d.get("package") or ""),
            )
        )
    groups = []
    for g in cfg.get("groups") or []:
        if not g.get("user") or not g.get("add"):
            raise ProvisionError(f"groups の user / add がありません: {g}")
        groups.append((str(g["user"]), tuple(str(x) for x in g["add"])))
    return Manifest(
        path=path,
        groups=tuple(groups),
        apt=tuple(str(x) for x in cfg.get("apt") or ()),
        python=tuple(str(x) for x in cfg.get("python") or ()),
        venv=str(cfg.get("venv") or "/opt/amig/venv"),
        binaries=tuple(bins),
        debs=tuple(debs),
        containers=tuple(cons),
        compose=tuple(comps),
    )


# ---- 実行 ----


@dataclass
class Runner:
    """外部コマンドの実行(dry-run と試験のためにここに集約する)。"""

    dry_run: bool = False
    log: list[list[str]] = field(default_factory=list)

    def run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        self.log.append(list(cmd))
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def ok(self, cmd: list[str]) -> bool:
        """成否だけ見る(照合用。失敗しても例外にしない)。"""
        try:
            return self.run(cmd, check=False).returncode == 0
        except OSError:
            return False


@dataclass
class Result:
    """項目1つの結果(成績書の1行)。"""

    kind: str
    name: str
    ok: bool
    detail: str = ""


# ---- 導入 ----


class Provisioner:
    """台帳のとおりに入れる/確かめる。"""

    def __init__(self, manifest: Manifest, runner: Runner | None = None) -> None:
        self.m = manifest
        self.r = runner or Runner()

    # -- apply --

    def apply(self) -> list[Result]:
        out: list[Result] = []
        if self.m.apt:
            out.append(self._apt())
        for b in self.m.binaries:
            out.append(self._binary(b))
            if b.service:
                out.append(self._service(b))
        for d in self.m.debs:
            out.append(self._deb(d))
        if self.m.python:
            out.append(self._python())
        for c in self.m.containers:
            out.append(self._container(c))
        for c in self.m.compose:
            out.append(self._compose(c))
        for user, groups in self.m.groups:
            out.append(self._groups(user, groups))
        return out

    def _deb(self, d: Deb) -> Result:
        """配布物を固定して取得し、依存解決は apt に任せて入れる。"""
        if not d.sha256:
            raise ProvisionError(
                f"{d.name}: sha256 が台帳にありません"
                "(--pin で一度だけ実測して台帳に記録してください)"
            )
        if self.r.dry_run:
            self.r.run(["apt-get", "install", "-y", f"./{d.name}.deb"])
            return Result("deb", d.name, True, "取得して入れる(dry-run)")
        with tempfile.TemporaryDirectory() as tmp:
            got = download(d.url, Path(tmp) / f"{d.name}.deb")
            actual = sha256_file(got)
            if actual != d.sha256:
                raise ProvisionError(
                    f"{d.name}: ハッシュが台帳と違います\n"
                    f"  台帳 {d.sha256}\n  実物 {actual}"
                )
            self.r.run(["apt-get", "install", "-y", str(got)])
        return Result("deb", d.name, True, d.pkg)

    def _groups(self, user: str, groups: tuple[str, ...]) -> Result:
        """使う人を機器の操作グループに入れる(USB の書き込み等)。

        これが無いと、道具は入っているのに実機に書けない箱ができる。
        納品前に必ず確かめる項目(§19 の受け入れ検査)。
        """
        self.r.run(["usermod", "-aG", ",".join(groups), user])
        return Result("groups", user, True, " ".join(groups))

    def _apt(self) -> Result:
        self.r.run(["apt-get", "update", "-qq"])
        self.r.run(["apt-get", "install", "-y", "-qq", *self.m.apt])
        return Result(
            "apt", f"{len(self.m.apt)} パッケージ", True, " ".join(self.m.apt)
        )

    def _binary(self, b: Binary) -> Result:
        if not b.sha256:
            raise ProvisionError(
                f"{b.name}: sha256 が台帳にありません"
                "(--pin で一度だけ実測して台帳に記録してください)"
            )
        dest = Path(b.dest)
        if dest.exists() and sha256_file(dest) == b.sha256:
            return Result("binary", b.name, True, "既に台帳どおり")
        if self.r.dry_run:
            return Result("binary", b.name, True, f"取得して {dest} に置く(dry-run)")
        with tempfile.TemporaryDirectory() as tmp:
            got = download(b.url, Path(tmp) / "dl")
            actual = sha256_file(got)
            if actual != b.sha256:
                raise ProvisionError(
                    f"{b.name}: ハッシュが台帳と違います\n"
                    f"  台帳 {b.sha256}\n  実物 {actual}"
                )
            src = _unpack(got, b, Path(tmp))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            dest.chmod(0o755)
        return Result("binary", b.name, True, str(dest))

    def _service(self, b: Binary) -> Result:
        """systemd のユニットを台帳から書き出して起動する。"""
        svc = b.service
        exec_start = str(svc.get("exec") or b.dest)
        user = str(svc.get("user") or "root")
        unit = (
            "[Unit]\n"
            f"Description={b.name}(amig provision)\n"
            "After=network.target\n\n"
            "[Service]\n"
            f"ExecStart={exec_start}\n"
            f"User={user}\n"
            "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        path = UNIT_DIR / f"{b.name}.service"
        if user != "root":
            self.r.run(
                [
                    "useradd",
                    "--system",
                    "--no-create-home",
                    "--shell",
                    "/usr/sbin/nologin",
                    user,
                ],
                check=False,
            )
        if not self.r.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(unit, encoding="utf-8")
        self.r.run(["systemctl", "daemon-reload"])
        self.r.run(["systemctl", "enable", "--now", f"{b.name}.service"])
        return Result("service", b.name, True, str(path))

    def _python(self) -> Result:
        venv = Path(self.m.venv)
        pip = venv / "bin" / "pip"
        if not pip.exists():
            self.r.run(["python3", "-m", "venv", str(venv)])
        self.r.run([str(pip), "install", "-q", "--upgrade", *self.m.python])
        return Result("python", f"{len(self.m.python)} パッケージ", True, str(venv))

    def _container(self, c: Container) -> Result:
        if not c.digest:
            raise ProvisionError(
                f"{c.name}: digest が台帳にありません"
                "(--pin で一度だけ実測して台帳に記録してください)"
            )
        ref = f"{c.image}@{c.digest}"
        self.r.run(["podman", "pull", ref])
        self.r.run(["podman", "rm", "-f", c.name], check=False)
        cmd = ["podman", "run", "-d", "--name", c.name, "--restart", "always"]
        for p in c.ports:
            cmd += ["-p", p]
        for k, v in c.env.items():
            cmd += ["-e", f"{k}={v}"]
        for v in c.volumes:
            cmd += ["-v", v]
        cmd.append(ref)
        self.r.run(cmd)
        # 再起動後も上がるように、systemd のユニットにして委ねる
        self.r.run(
            ["podman", "generate", "systemd", "--new", "--files", "--name", c.name],
            check=False,
        )
        return Result("container", c.name, True, ref)

    def _compose(self, c: Compose) -> Result:
        """上流の compose 定義を固定して取得し、そのまま起動する。"""
        if not c.sha256:
            raise ProvisionError(
                f"{c.name}: sha256 が台帳にありません"
                "(--pin で一度だけ実測して台帳に記録してください)"
            )
        d = Path(c.dir)
        target = d / "compose.yaml"
        if not self.r.dry_run:
            d.mkdir(parents=True, exist_ok=True)
            if not (target.exists() and sha256_file(target) == c.sha256):
                got = download(c.url, d / ".compose.dl")
                actual = sha256_file(got)
                if actual != c.sha256:
                    got.unlink(missing_ok=True)
                    raise ProvisionError(
                        f"{c.name}: compose 定義のハッシュが台帳と違います\n"
                        f"  台帳 {c.sha256}\n  実物 {actual}"
                    )
                got.replace(target)
            if c.env:
                # 秘密は台帳に書かない。初回に生成した値は .env に残る
                (d / ".env").write_text(
                    "".join(f"{k}={v}\n" for k, v in c.env.items()), encoding="utf-8"
                )
        self.r.run(["podman-compose", "-f", str(target), "up", "-d"])
        return Result("compose", c.name, True, str(target))

    # -- check --

    def check(self) -> list[Result]:
        """入っている物と台帳を突き合わせる(変更しない)。"""
        out: list[Result] = []
        for pkg in self.m.apt:
            ok = self.r.ok(["dpkg", "-s", pkg])
            out.append(Result("apt", pkg, ok, "導入済み" if ok else "**未導入**"))
        for b in self.m.binaries:
            dest = Path(b.dest)
            if not dest.exists():
                out.append(Result("binary", b.name, False, "**ファイルがありません**"))
            else:
                actual = sha256_file(dest)
                ok = actual == b.sha256
                out.append(
                    Result(
                        "binary",
                        b.name,
                        ok,
                        "台帳どおり" if ok else f"**ハッシュ相違** {actual}",
                    )
                )
            if b.service:
                ok = self.r.ok(
                    ["systemctl", "is-active", "--quiet", f"{b.name}.service"]
                )
                out.append(Result("service", b.name, ok, "稼働" if ok else "**停止**"))
        for d in self.m.debs:
            ok = self.r.ok(["dpkg", "-s", d.pkg])
            out.append(Result("deb", d.name, ok, "導入済み" if ok else "**未導入**"))
        venv_py = Path(self.m.venv) / "bin" / "python"
        for pkg in self.m.python:
            name = _dist_name(pkg)
            ok = self.r.ok(
                [
                    str(venv_py),
                    "-c",
                    f"import importlib.metadata as m;m.version('{name}')",
                ]
            )
            out.append(Result("python", name, ok, "導入済み" if ok else "**未導入**"))
        for c in self.m.containers:
            ok = self.r.ok(["podman", "container", "exists", c.name])
            out.append(Result("container", c.name, ok, "稼働" if ok else "**停止**"))
        for user, groups in self.m.groups:
            res = self.r.run(["id", "-Gn", user], check=False)
            have = set((res.stdout or "").split())
            missing = [g for g in groups if g not in have]
            out.append(
                Result(
                    "groups",
                    user,
                    not missing,
                    " ".join(groups)
                    if not missing
                    else f"**未所属** {' '.join(missing)}",
                )
            )
        for c in self.m.compose:
            target = Path(c.dir) / "compose.yaml"
            if not target.exists():
                out.append(Result("compose", c.name, False, "**定義がありません**"))
                continue
            ok = sha256_file(target) == c.sha256
            out.append(
                Result(
                    "compose", c.name, ok, "台帳どおり" if ok else "**ハッシュ相違**"
                )
            )
        return out

    # -- pin --

    def pin(self) -> list[Result]:
        """取得物のハッシュ・ダイジェストを実測し、台帳に書き込む。

        一度だけ行う作業(先に表、後に線)。書き込んだ台帳をリポジトリに
        入れれば、以後の導入は毎回この値と突き合わせる。
        """
        cfg = yaml.safe_load(self.m.path.read_text(encoding="utf-8")) or {}
        out: list[Result] = []
        for i, b in enumerate(self.m.binaries):
            if b.sha256:
                out.append(Result("binary", b.name, True, "固定済み"))
                continue
            with tempfile.TemporaryDirectory() as tmp:
                got = download(b.url, Path(tmp) / "dl")
                h = sha256_file(got)
            cfg["binaries"][i]["sha256"] = h
            out.append(Result("binary", b.name, True, h))
        for i, d in enumerate(self.m.debs):
            if d.sha256:
                out.append(Result("deb", d.name, True, "固定済み"))
                continue
            with tempfile.TemporaryDirectory() as tmp:
                got = download(d.url, Path(tmp) / "dl")
                h = sha256_file(got)
            cfg["deb"][i]["sha256"] = h
            out.append(Result("deb", d.name, True, h))
        for i, c in enumerate(self.m.containers):
            if c.digest:
                out.append(Result("container", c.name, True, "固定済み"))
                continue
            self.r.run(["podman", "pull", c.image])
            res = self.r.run(
                ["podman", "image", "inspect", c.image, "--format", "{{json .Digest}}"]
            )
            digest = json.loads(res.stdout or '""') if res.stdout else ""
            if not digest:
                raise ProvisionError(f"{c.name}: ダイジェストを取得できません")
            cfg["containers"][i]["digest"] = digest
            out.append(Result("container", c.name, True, digest))
        for i, c in enumerate(self.m.compose):
            if c.sha256:
                out.append(Result("compose", c.name, True, "固定済み"))
                continue
            with tempfile.TemporaryDirectory() as tmp:
                got = download(c.url, Path(tmp) / "dl")
                h = sha256_file(got)
            cfg["compose"][i]["sha256"] = h
            out.append(Result("compose", c.name, True, h))
        if not self.r.dry_run:
            self.m.path.write_text(
                yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        return out


# ---- 補助 ----


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> Path:
    """取得だけ行う(照合は呼び出し側)。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as res:  # noqa: S310
        dest.write_bytes(res.read())
    return dest


def _unpack(got: Path, b: Binary, tmp: Path) -> Path:
    """書庫なら取り出す。member 未指定なら name と同じ名前を探す。"""
    if not b.archive:
        return got
    want = b.member or b.name
    outdir = tmp / "x"
    outdir.mkdir(exist_ok=True)
    if b.archive == "zip":
        with zipfile.ZipFile(got) as z:
            z.extractall(outdir)
    elif b.archive in ("tar.gz", "tgz"):
        with tarfile.open(got) as t:
            t.extractall(outdir, filter="data")
    else:
        raise ProvisionError(f"{b.name}: 未対応の書庫形式 {b.archive}")
    for f in sorted(outdir.rglob("*")):
        if f.is_file() and f.name == Path(want).name:
            return f
    raise ProvisionError(f"{b.name}: 書庫の中に {want} がありません")


def _dist_name(spec: str) -> str:
    """pip の指定から配布名を取り出す(バージョン・追加指定を落とす)。"""
    for sep in ("[", "==", ">=", "<=", "~=", ">", "<"):
        spec = spec.split(sep)[0]
    return spec.strip()


def receipt(manifest: Manifest, results: list[Result], mode: str) -> str:
    """成績書(受け入れ検査の結果。§19 の納品物)。"""
    ok = all(r.ok for r in results)
    lines = [
        "# 受け入れ検査 成績書",
        "",
        f"台帳: `{manifest.path}`",
        f"作業: {mode}",
        f"結果: **{'合格' if ok else '不合格(下表の太字を参照)'}**"
        f"({sum(r.ok for r in results)}/{len(results)} 項目)",
        "",
        "| 種別 | 項目 | 判定 | 内容 |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.kind} | {r.name} | {'○' if r.ok else '×'} | {r.detail} |")
    lines += [
        "",
        "この成績書は台帳と実機を突き合わせて機械が出したものである。",
        "施工と検査は別の仕事であり、検査を省いた納品は壁の中に故障を封印する。",
        "",
    ]
    return "\n".join(lines)


def require_root(mode: str) -> None:
    if os.geteuid() != 0:
        raise ProvisionError(
            f"{mode} には管理者権限が要ります(sudo で実行してください)。"
            "確認だけなら --check または --dry-run を使ってください"
        )
