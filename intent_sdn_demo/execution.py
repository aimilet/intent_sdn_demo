"""Mininet 验证执行器：只执行内部模板生成的固定流表和 QoS 动作。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from threading import Lock

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import CandidatePlan, MetricSnapshot, TrafficMetrics


_PING_TIME_PATTERN = re.compile(r"time[=<]([0-9.]+)\s*ms")
_IPERF_THROUGHPUT_PATTERN = re.compile(r"([0-9.]+)\s+Mbits/sec")
_IPERF_LOSS_PATTERN = re.compile(r"\(([0-9.]+)%\)")
_PORT_TX_BYTES_PATTERN = re.compile(r"tx pkts=\d+, bytes=(\d+)")
_OVS_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_OWNER = "intent-sdn-demo"
_PATH_CAPACITY_MBPS = {"low_latency": 20.0, "high_capacity": 50.0}
_VIDEO_METER_ID = 2
_VIDEO_MAX_RATE_MBPS = 8.0
# iperf 的窗口统计和 OpenFlow token bucket 都会产生极小波动；超过该值视为限速未生效。
_VIDEO_MAX_RATE_TOLERANCE_MBPS = 0.5
_SWITCH_DPIDS = {
    "rsu": "0000000000000001",
    "low": "0000000000000002",
    "high": "0000000000000003",
    "edge": "0000000000000004",
}
_HOST_NAMES = {
    "emergency": "ve",
    "control": "vc",
    "navigation": "vn",
    "video": "vv",
    "edge": "edge",
}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Ports:
    """一次性验证拓扑中的受控 OVS 端口编号和名称。"""

    rsu_to_low: int
    rsu_to_high: int
    rsu_to_emergency: int
    rsu_to_control: int
    rsu_to_navigation: int
    rsu_to_video: int
    low_to_rsu: int
    low_to_edge_switch: int
    high_to_rsu: int
    high_to_edge_switch: int
    edge_switch_to_low: int
    edge_switch_to_high: int
    edge_switch_to_edge: int
    low_interface: str
    high_interface: str


@dataclass
class _CreatedPolicyResources:
    """记录本次已成功创建的临时资源，确保异常路径也能准确清理。"""

    video_meter_id: int | None = None


class MininetExecutor:
    """创建临时两路径拓扑并执行单次验证；每次结束均停止网络以避免残留。"""

    def __init__(self) -> None:
        """串行化临时拓扑执行，避免并发请求复用相同的 OVS 节点名。"""

        self._execution_lock = Lock()

    def execute(self, plan: CandidatePlan) -> MetricSnapshot:
        """在同一压力场景下依次采集基线与已确认策略的实测指标。"""

        with self._execution_lock:
            LOGGER.info("开始 Mininet 临时验证：计划=%s", plan.plan_id)
            return self._execute_once(plan)

    def _execute_once(self, plan: CandidatePlan) -> MetricSnapshot:
        """在独立临时拓扑中完成一次不可并发的基线与策略验证。"""

        self._validate_environment()
        try:
            from mininet.link import TCLink
            from mininet.net import Mininet
            from mininet.node import OVSSwitch
        except ImportError as exc:
            raise IntentError("mininet_missing", "当前环境未安装 Mininet Python 运行库。", 503) from exc

        network = Mininet(
            controller=None,
            switch=OVSSwitch,
            link=TCLink,
            build=False,
            autoSetMacs=True,
        )
        switches: dict[str, object] = {}
        ports: _Ports | None = None
        result: MetricSnapshot | None = None
        execution_error: IntentError | None = None
        execution_cause: Exception | None = None
        created_resources = _CreatedPolicyResources()
        try:
            hosts, switches, links = self._build_topology(network)
            network.build()
            network.start()
            self._install_static_arp(hosts)
            ports = self._ports(switches, links)
            self._install_baseline_flows(switches, ports)
            self._start_iperf_servers(hosts)
            baseline = self._measure(hosts, switches["rsu"], ports)
            if plan.plan_id == "baseline":
                applied = baseline
            else:
                self._apply_plan(plan, switches["rsu"], ports, created_resources)
                applied = self._measure(hosts, switches["rsu"], ports)
                self._validate_applied_metrics(plan, applied)
            result = MetricSnapshot(plan_id=plan.plan_id, baseline=baseline, applied=applied)
        except IntentError as exc:
            LOGGER.exception("Mininet 验证被中止：计划=%s", plan.plan_id)
            execution_error = exc
        except Exception as exc:
            LOGGER.exception("Mininet 验证发生内部异常：计划=%s", plan.plan_id)
            execution_error = IntentError(
                "mininet_execution_failed",
                "Mininet 策略验证失败，已停止本次临时拓扑。",
                503,
            )
            execution_cause = exc
        finally:
            try:
                self._clear_qos(
                    switches.get("rsu"),
                    ports,
                    video_meter_id=created_resources.video_meter_id,
                )
            except Exception as cleanup_exc:
                LOGGER.exception("临时 QoS 清理失败，将继续停止 Mininet 拓扑。")
                if execution_error is None:
                    execution_error = IntentError(
                        "mininet_cleanup_failed",
                        "Mininet 策略验证后的 QoS 清理失败，已停止临时拓扑。",
                        503,
                    )
                    execution_cause = cleanup_exc
            finally:
                network.stop()
        if execution_error is not None:
            if execution_cause is not None:
                raise execution_error from execution_cause
            raise execution_error
        if result is None:
            raise IntentError("mininet_execution_failed", "Mininet 未生成策略验证结果。", 503)
        return result

    def reset(self) -> dict[str, str]:
        """临时拓扑每次执行后均已停止，因此重置只确认不存在持久化策略状态。"""

        return {
            "status": "reset",
            "message": "临时 Mininet 拓扑已在每次验证后停止，不存在持久化流表或 QoS。",
        }

    def _validate_environment(self) -> None:
        """在创建网络命名空间前检查 root 权限和必要命令，避免部分下发。"""

        if os.geteuid() != 0:
            raise IntentError("mininet_permission_required", "Mininet 验证需要以 root 权限启动本地服务。", 503)
        missing = [name for name in ("mn", "ovs-ofctl", "ovs-vsctl", "iperf") if not shutil.which(name)]
        if missing:
            raise IntentError("mininet_dependency_missing", f"缺少 Mininet 依赖：{', '.join(missing)}。", 503)

    def _build_topology(self, network):
        """构建四类车辆、RSU、双核心路径和边缘节点的固定验证拓扑。"""

        # Link 默认生成“节点名-ethN”；Linux 接口名最多 15 个可见字符，内部名必须简短。
        emergency = network.addHost(_HOST_NAMES["emergency"], ip="10.0.0.11/24")
        control = network.addHost(_HOST_NAMES["control"], ip="10.0.0.12/24")
        navigation = network.addHost(_HOST_NAMES["navigation"], ip="10.0.0.13/24")
        video = network.addHost(_HOST_NAMES["video"], ip="10.0.0.14/24")
        edge = network.addHost(_HOST_NAMES["edge"], ip="10.0.0.100/24")
        # Mininet 只会从 s1 一类规范名称推导 DPID；展示名称需显式给出稳定十六进制 DPID。
        rsu = network.addSwitch(
            "rsu",
            dpid=_SWITCH_DPIDS["rsu"],
            protocols="OpenFlow13",
            failMode="secure",
        )
        low = network.addSwitch(
            "low",
            dpid=_SWITCH_DPIDS["low"],
            protocols="OpenFlow13",
            failMode="secure",
        )
        high = network.addSwitch(
            "high",
            dpid=_SWITCH_DPIDS["high"],
            protocols="OpenFlow13",
            failMode="secure",
        )
        edge_switch = network.addSwitch(
            "edgeSwitch",
            dpid=_SWITCH_DPIDS["edge"],
            protocols="OpenFlow13",
            failMode="secure",
        )

        links = {
            "emergency": network.addLink(emergency, rsu, bw=100, delay="1ms"),
            "control": network.addLink(control, rsu, bw=100, delay="1ms"),
            "navigation": network.addLink(navigation, rsu, bw=100, delay="1ms"),
            "video": network.addLink(video, rsu, bw=100, delay="1ms"),
            "rsu_low": network.addLink(rsu, low, bw=20, delay="5ms"),
            "low_edge": network.addLink(low, edge_switch, bw=20, delay="5ms"),
            "rsu_high": network.addLink(rsu, high, bw=50, delay="15ms"),
            "high_edge": network.addLink(high, edge_switch, bw=50, delay="15ms"),
            "edge": network.addLink(edge_switch, edge, bw=100, delay="1ms"),
        }
        return (
            {
                "emergency": emergency,
                "control": control,
                "navigation": navigation,
                "video": video,
                "edge": edge,
            },
            {"rsu": rsu, "low": low, "high": high, "edge": edge_switch},
            links,
        )

    def _ports(self, switches, links) -> _Ports:
        """从 Mininet Link 对象获取端口，避免将端口号写死到执行逻辑。"""

        rsu = switches["rsu"]
        low = switches["low"]
        high = switches["high"]
        edge_switch = switches["edge"]
        rsu_low_intf = _interface_for(links["rsu_low"], rsu)
        rsu_high_intf = _interface_for(links["rsu_high"], rsu)
        return _Ports(
            rsu_to_low=_port_for(rsu_low_intf),
            rsu_to_high=_port_for(rsu_high_intf),
            rsu_to_emergency=_port_for(_interface_for(links["emergency"], rsu)),
            rsu_to_control=_port_for(_interface_for(links["control"], rsu)),
            rsu_to_navigation=_port_for(_interface_for(links["navigation"], rsu)),
            rsu_to_video=_port_for(_interface_for(links["video"], rsu)),
            low_to_rsu=_port_for(_interface_for(links["rsu_low"], low)),
            low_to_edge_switch=_port_for(_interface_for(links["low_edge"], low)),
            high_to_rsu=_port_for(_interface_for(links["rsu_high"], high)),
            high_to_edge_switch=_port_for(_interface_for(links["high_edge"], high)),
            edge_switch_to_low=_port_for(_interface_for(links["low_edge"], edge_switch)),
            edge_switch_to_high=_port_for(_interface_for(links["high_edge"], edge_switch)),
            edge_switch_to_edge=_port_for(_interface_for(links["edge"], edge_switch)),
            low_interface=rsu_low_intf.name,
            high_interface=rsu_high_intf.name,
        )

    def _install_static_arp(self, hosts) -> None:
        """预置固定主机的 ARP 表，避免删除基线流表后广播 ARP 破坏 IP 测量。"""

        for source in hosts.values():
            for target in hosts.values():
                if source is not target:
                    source.setARP(target.IP(), target.MAC())

    def _install_baseline_flows(self, switches, ports: _Ports) -> None:
        """安装只由固定 IP 和端口构成的双向基线流表，所有前向流默认走低时延路径。"""

        rsu = switches["rsu"]
        low = switches["low"]
        high = switches["high"]
        edge_switch = switches["edge"]
        for switch in switches.values():
            _run_checked(switch, f"ovs-ofctl -O OpenFlow13 del-flows {switch.name}")

        _add_flow(rsu, "ip,nw_dst=10.0.0.100", f"output:{ports.rsu_to_low}")
        _add_flow(low, "ip,nw_dst=10.0.0.100", f"output:{ports.low_to_edge_switch}")
        _add_flow(high, "ip,nw_dst=10.0.0.100", f"output:{ports.high_to_edge_switch}")
        _add_flow(edge_switch, "ip,nw_dst=10.0.0.100", f"output:{ports.edge_switch_to_edge}")

        vehicle_ports = {
            "10.0.0.11": ports.rsu_to_emergency,
            "10.0.0.12": ports.rsu_to_control,
            "10.0.0.13": ports.rsu_to_navigation,
            "10.0.0.14": ports.rsu_to_video,
        }
        for address, rsu_port in vehicle_ports.items():
            _add_flow(rsu, f"ip,nw_dst={address}", f"output:{rsu_port}")
            _add_flow(low, f"ip,nw_dst={address}", f"output:{ports.low_to_rsu}")
            _add_flow(high, f"ip,nw_dst={address}", f"output:{ports.high_to_rsu}")
            _add_flow(edge_switch, f"ip,nw_dst={address}", f"output:{ports.edge_switch_to_low}")

    def _apply_plan(
        self,
        plan: CandidatePlan,
        rsu,
        ports: _Ports,
        created_resources: _CreatedPolicyResources,
    ) -> None:
        """根据预览模板下发固定流表、队列和计量器，并记录已创建的资源。"""

        if plan.plan_id in {"critical_priority", "combined"}:
            self._configure_qos(
                rsu,
                ports.low_interface,
                queue_id=1,
                min_rate=12,
                max_rate=20,
                root_max_rate=int(_PATH_CAPACITY_MBPS["low_latency"]),
            )
            _add_flow(
                rsu,
                "ip,nw_dst=10.0.0.100,udp,tp_dst=5001",
                f"set_queue:1,output:{ports.rsu_to_low}",
                priority=200,
            )
        if plan.plan_id in {"congestion_relief", "combined"}:
            self._configure_qos(
                rsu,
                ports.high_interface,
                queue_id=2,
                min_rate=1,
                max_rate=int(_VIDEO_MAX_RATE_MBPS),
                root_max_rate=int(_PATH_CAPACITY_MBPS["high_capacity"]),
            )
            # TCLink 已创建的根 qdisc 在部分环境中会令 OVS 队列只完成选队不生效。
            # OpenFlow meter 是独立的数据面约束，确保模板的 8 Mbps 上限不会被静默突破。
            _add_meter(rsu, _VIDEO_METER_ID, _VIDEO_MAX_RATE_MBPS)
            created_resources.video_meter_id = _VIDEO_METER_ID
            _add_flow(
                rsu,
                "ip,nw_dst=10.0.0.100,udp,tp_dst=5004",
                f"meter:{_VIDEO_METER_ID},set_queue:2,output:{ports.rsu_to_high}",
                priority=200,
            )

    def _configure_qos(
        self,
        rsu,
        interface: str,
        *,
        queue_id: int,
        min_rate: int,
        max_rate: int,
        root_max_rate: int,
    ) -> None:
        """以 Linux HTB 创建命名队列，根队列保持对应物理路径的固定容量。"""

        command = (
            "ovs-vsctl "
            f"-- --id=@queue create Queue external_ids:owner={_OWNER} "
            f"other-config:min-rate={min_rate * 1_000_000} other-config:max-rate={max_rate * 1_000_000} "
            f"-- --id=@qos create QoS type=linux-htb external_ids:owner={_OWNER} "
            f"other-config:max-rate={root_max_rate * 1_000_000} queues:{queue_id}=@queue "
            f"-- set Port {interface} qos=@qos"
        )
        _run_checked(rsu, command)

    def _validate_applied_metrics(self, plan: CandidatePlan, metrics: TrafficMetrics) -> None:
        """校验具有确定量化承诺的策略结果，禁止将未生效策略报告为成功。"""

        if plan.plan_id not in {"congestion_relief", "combined"}:
            return
        video_throughput = metrics.throughput_mbps["video"]
        allowed_rate = _VIDEO_MAX_RATE_MBPS + _VIDEO_MAX_RATE_TOLERANCE_MBPS
        if video_throughput > allowed_rate:
            raise IntentError(
                "policy_effectiveness_failed",
                (
                    "背景视频限速未达到模板承诺："
                    f"实测 {video_throughput:.2f} Mbps，允许上限 {allowed_rate:.2f} Mbps。"
                ),
                503,
            )

    def _start_iperf_servers(self, hosts) -> None:
        """在边缘节点启动固定端口的 UDP 服务端，端口仅来自受控拓扑清单。"""

        edge = hosts["edge"]
        for port in (5001, 5002, 5003, 5004):
            _run_checked(
                edge,
                f"iperf -s -u -p {port} > /tmp/{_OWNER}-{port}.log 2>&1",
                background=True,
            )

    def _measure(self, hosts, rsu, ports: _Ports) -> TrafficMetrics:
        """在背景视频压力下读取 RSU 出口计数，并采集各业务端到端实测指标。"""

        video = hosts["video"]
        started_at = time.monotonic()
        start_bytes = self._path_tx_bytes(rsu, ports)
        video.cmd(
            "iperf -c 10.0.0.100 -u -p 5004 -b 18M -t 7 "
            f"> /tmp/{_OWNER}-video-client.log 2>&1 &"
        )
        time.sleep(0.5)
        ping_output = hosts["emergency"].cmd("ping -c 15 -i 0.05 10.0.0.100")
        outputs = {
            "emergency": hosts["emergency"].cmd("iperf -c 10.0.0.100 -u -p 5001 -b 5M -t 2"),
            "control": hosts["control"].cmd("iperf -c 10.0.0.100 -u -p 5002 -b 3M -t 2"),
            "navigation": hosts["navigation"].cmd("iperf -c 10.0.0.100 -u -p 5003 -b 2M -t 2"),
        }
        time.sleep(2)
        outputs["video"] = video.cmd(f"cat /tmp/{_OWNER}-video-client.log")
        elapsed_seconds = time.monotonic() - started_at
        end_bytes = self._path_tx_bytes(rsu, ports)
        throughput = {traffic: _parse_throughput(output) for traffic, output in outputs.items()}
        loss = {traffic: _parse_loss(output) for traffic, output in outputs.items()}
        return TrafficMetrics(
            emergency_p95_latency_ms=_p95_ping_latency(ping_output),
            throughput_mbps=throughput,
            packet_loss_percent=loss,
            link_utilization_percent=_path_utilization_percent(
                start_bytes,
                end_bytes,
                elapsed_seconds,
            ),
        )

    def _path_tx_bytes(self, rsu, ports: _Ports) -> dict[str, int]:
        """读取 RSU 两个核心出口的 OVS TX 字节计数，作为路径利用率唯一数据源。"""

        return {
            "low_latency": _read_port_tx_bytes(rsu, ports.rsu_to_low),
            "high_capacity": _read_port_tx_bytes(rsu, ports.rsu_to_high),
        }

    def _clear_qos(
        self,
        rsu,
        ports: _Ports | None,
        *,
        video_meter_id: int | None = None,
    ) -> None:
        """清理本次命名 QoS、队列和已创建计量器，避免临时状态残留。"""

        if rsu is None or ports is None:
            return
        if video_meter_id is not None:
            _delete_meter(rsu, video_meter_id)
        for interface in (ports.low_interface, ports.high_interface):
            _run_checked(rsu, f"ovs-vsctl --if-exists clear Port {interface} qos")
        queue_ids = _owned_resource_ids(rsu, "Queue")
        qos_ids = _owned_resource_ids(rsu, "QoS")
        if qos_ids:
            _run_checked(rsu, f"ovs-vsctl --if-exists destroy QoS {' '.join(qos_ids)}")
        if queue_ids:
            _run_checked(rsu, f"ovs-vsctl --if-exists destroy Queue {' '.join(queue_ids)}")
        if _owned_resource_ids(rsu, "Queue") or _owned_resource_ids(rsu, "QoS"):
            raise RuntimeError("本次验证创建的 QoS 或 Queue 记录仍存在。")


def _interface_for(link, node):
    """从 Link 中取得指定节点侧接口，节点不匹配时立即暴露拓扑构造错误。"""

    if link.intf1.node == node:
        return link.intf1
    if link.intf2.node == node:
        return link.intf2
    raise RuntimeError("链路不属于指定交换节点。")


def _port_for(interface) -> int:
    """读取 Mininet 为接口分配的 OpenFlow 端口号。"""

    return int(interface.node.ports[interface])


def _add_flow(switch, match: str, actions: str, *, priority: int = 100) -> None:
    """下发内部固定流表；调用点仅传入代码常量或受控端口整数。"""

    _run_checked(
        switch,
        f"ovs-ofctl -O OpenFlow13 add-flow {switch.name} "
        f"'priority={priority},{match},actions={actions}'",
    )


def _add_meter(switch, meter_id: int, max_rate_mbps: float) -> None:
    """创建仅供背景视频流使用的固定 OpenFlow 1.3 丢弃型计量器。"""

    rate_kbps = int(max_rate_mbps * 1_000)
    _run_checked(
        switch,
        f"ovs-ofctl -O OpenFlow13 add-meter {switch.name} "
        f"'meter={meter_id},kbps,band=type=drop,rate={rate_kbps}'",
    )


def _delete_meter(switch, meter_id: int) -> None:
    """删除本次已成功创建的固定计量器，避免临时桥状态残留。"""

    # ovs-ofctl 的单计量器删除参数沿用 Meter Syntax，必须为 meter=<id>，不能传裸数字。
    _run_checked(
        switch,
        f"ovs-ofctl -O OpenFlow13 del-meters {switch.name} meter={meter_id}",
    )


def _run_checked(node, command: str, *, background: bool = False) -> str:
    """以单行脚本运行内部固定命令并检查状态，兼容 Mininet 的提示符完成语义。"""

    marker = "__intent_sdn_status__"
    if background:
        script = f"{command} & status=$?; printf '{marker}%s' $status"
    else:
        script = f"{command}; status=$?; printf '{marker}%s' $status"
    output = node.cmd(script)
    position = output.rfind(marker)
    if position < 0:
        raise RuntimeError("未取得受控命令的退出状态。")
    status = output[position + len(marker) :].strip()
    if status != "0":
        diagnostic = " ".join(output[:position].split())[-500:]
        detail = f" 输出：{diagnostic}" if diagnostic else ""
        raise RuntimeError(f"受控网络命令执行失败。{detail}")
    return output[:position]


def _owned_resource_ids(rsu, table: str) -> tuple[str, ...]:
    """读取并校验本次所有者标记的 OVSDB UUID，禁止将未验证输出回拼到命令。"""

    output = _run_checked(
        rsu,
        "ovs-vsctl --data=bare --no-heading --columns=_uuid "
        f"find {table} external_ids:owner={_OWNER}",
    )
    identifiers = tuple(item for item in output.split() if item)
    if any(_OVS_UUID_PATTERN.fullmatch(item) is None for item in identifiers):
        raise RuntimeError("OVSDB 返回了不合法的资源标识。")
    return identifiers


def _read_port_tx_bytes(switch, port: int) -> int:
    """读取固定 OpenFlow 端口的 TX 字节数，缺少计数时中止而不伪造路径利用率。"""

    output = _run_checked(
        switch,
        f"ovs-ofctl -O OpenFlow13 dump-ports {switch.name} {port}",
    )
    match = _PORT_TX_BYTES_PATTERN.search(output)
    if match is None:
        raise RuntimeError("OVS 端口统计中缺少 TX 字节计数。")
    return int(match.group(1))


def _path_utilization_percent(
    start_bytes: dict[str, int],
    end_bytes: dict[str, int],
    elapsed_seconds: float,
) -> dict[str, float]:
    """将 RSU 出口 TX 字节增量换算为测试窗口内的平均链路利用率。"""

    if elapsed_seconds <= 0:
        raise RuntimeError("路径统计窗口时长必须大于 0。")
    utilization: dict[str, float] = {}
    for path, capacity_mbps in _PATH_CAPACITY_MBPS.items():
        try:
            byte_delta = end_bytes[path] - start_bytes[path]
        except KeyError as exc:
            raise RuntimeError("路径统计缺少核心出口计数。") from exc
        if byte_delta < 0:
            raise RuntimeError("OVS 端口 TX 字节计数发生回退。")
        usage = byte_delta * 8 / elapsed_seconds / 1_000_000 / capacity_mbps * 100
        utilization[path] = round(min(100.0, usage), 2)
    return utilization


def _p95_ping_latency(output: str) -> float | None:
    """从 ping 明细计算 P95；没有成功样本时返回 None。"""

    values = sorted(float(item) for item in _PING_TIME_PATTERN.findall(output))
    if not values:
        return None
    index = min(len(values) - 1, max(0, int(len(values) * 0.95 + 0.9999) - 1))
    return round(values[index], 3)


def _parse_throughput(output: str) -> float:
    """提取 iperf 最后一段 Mbps 吞吐量，解析失败时保守返回 0。"""

    matches = _IPERF_THROUGHPUT_PATTERN.findall(output)
    return round(float(matches[-1]), 3) if matches else 0.0


def _parse_loss(output: str) -> float:
    """提取 UDP iperf 最后一段丢包率，未报告时返回 0 供页面明确展示。"""

    matches = _IPERF_LOSS_PATTERN.findall(output)
    return round(float(matches[-1]), 3) if matches else 0.0
