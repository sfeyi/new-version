"""启动导拍页；存在成员 2 的独立环境时，同时启动本机推荐接口。"""
import json
from ipaddress import IPv4Address
import os
from pathlib import Path
import socket
import subprocess
from urllib.error import URLError
from urllib.request import urlopen

from app import app


ROOT = Path(__file__).resolve().parent


def lan_ipv4_addresses():
    """Prefer active Wi-Fi/Ethernet adapters, excluding common virtual adapters."""
    candidates = []
    if os.name == "nt":
        command = (
            "[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() "
            "| Where-Object { $_.OperationalStatus -eq 'Up' -and "
            "$_.NetworkInterfaceType.ToString() -in @('Ethernet', 'Wireless80211') -and "
            "$_.Description -notmatch 'Virtual|VMware|Hyper-V|VPN|Tunnel|TAP|Loopback' } "
            "| ForEach-Object { $_.GetIPProperties().UnicastAddresses } "
            "| Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } "
            "| ForEach-Object { $_.Address.ToString() }"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, errors="replace", timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                candidates.extend(result.stdout.splitlines())
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not candidates:
        try:
            candidates.extend(item[4][0] for item in socket.getaddrinfo(
                socket.gethostname(), None, family=socket.AF_INET, type=socket.SOCK_STREAM))
        except OSError:
            pass
    addresses = set()
    for value in candidates:
        try:
            address = IPv4Address(value.strip())
        except ValueError:
            continue
        if not (address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or address.is_reserved):
            addresses.add(str(address))
    return sorted(addresses, key=IPv4Address)


def print_connection_info(host, port):
    if host in {"127.0.0.1", "localhost", "::1"}:
        return
    addresses = lan_ipv4_addresses() if host in {"0.0.0.0", "::"} else [host]
    print("\n手机 APP 连接电脑服务：", flush=True)
    for address in addresses:
        print(f"  http://{address}:{port}", flush=True)
    if not addresses:
        print(f"  未找到局域网 IPv4，请用 ipconfig 查看 Wi-Fi 地址，填写 http://电脑IP:{port}", flush=True)
    print("在 APP 左上角设置中的电脑服务连接栏填写以上地址；请核对正在使用的 Wi-Fi/热点网卡。", flush=True)
    print("手机与电脑连接同一 Wi-Fi，或让电脑连接手机热点。电脑须保持开机，勿关闭本窗口。", flush=True)
    print("如 Windows 防火墙询问，仅按需允许 Python 访问专用网络；无需开放推荐服务的 8001 端口。\n", flush=True)


def launch_recommendation():
    url = os.environ.get("PHOTO_RECOMMEND_URL", "http://127.0.0.1:8001").rstrip("/")
    if url != "http://127.0.0.1:8001":
        return None, None
    try:
        with urlopen(url + "/health", timeout=1) as response:
            if json.load(response).get("status") == "ok":
                return None, None
    except (URLError, OSError, ValueError):
        pass
    project = Path(os.environ.get("CAMPUS_RECOMMEND_PROJECT", ROOT.parent / "campus-photo-recommend")).resolve()
    python = project / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if not python.is_file() or not (project / "photo_recommend/api.py").is_file():
        print("AI 推荐环境尚未准备；图库筛选和导拍可以使用。参见 README 的推荐服务配置。", flush=True)
        return None, None
    logs = ROOT / "build/logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = (logs / "recommend.log").open("a", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1")
    env["PYTHONPATH"] = str(project) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("OMP_NUM_THREADS", "4")
    env.setdefault("MKL_NUM_THREADS", "4")
    env.setdefault("PHOTO_RECOMMEND_CATALOG", str(project / "data/formal/catalog.json"))
    process = subprocess.Popen([str(python), "-m", "uvicorn", "scene_recommend:app", "--host", "127.0.0.1", "--port", "8001"],
                               cwd=ROOT, env=env, stdout=log, stderr=log,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print("推荐服务正在启动，日志：" + str(logs / "recommend.log"), flush=True)
    return process, log


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "7861"))
    print_connection_info(host, port)
    process, log = launch_recommendation()
    try:
        app.run(host=host, port=port, threaded=True)
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if log:
            log.close()
