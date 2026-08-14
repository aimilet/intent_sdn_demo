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
_OWNER = "intent-sdn-demo"
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
        try:
            hosts, switches, links = self._build_topology(network)
            network.build()
            network.start()
            self._install_static_arp(hosts)
            ports = self._ports(switches, links)
            self._install_baseline_flows(switches, ports)
            self._start_iperf_servers(hosts)
            baseline = self._measure(hosts)
            if plan.plan_id == "baseline":
                applied = baseline
            else:
                self._apply_plan(plan, switches["rsu"], ports)
                applied = self._measure(hosts)
            return MetricSnapshot(plan_id=plan.plan_id, baseline=baseline, applied=applied)
        except IntentError:
            LOGGER.exception("Mininet 验证被中止：计划=%s", plan.plan_id)
            raise
        except Exception as exc:
            LOGGER.exception("Mininet 验证发生内部异常：计划=%s", plan.plan_id)
            raise IntentError("mininet_execution_failed", "Mininet 策略验证失败，已停止本次临时拓扑。", 503) from exc
        finally:
            try:
                self._clear_qos(
                    switches.get("rsu") if "switches" in locals() else None,
                    locals().get("ports"),
                )
            except Exception:
                LOGGER.exception("临时 QoS 清理失败，将继续停止 Mininet 拓扑。")
            finally:
                network.stop()

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

        emergency = network.addHost("vehEmergency", ip="10.0.0.11/24")
        control = network.addHost("vehControl", ip="10.0.0.12/24")
        navigation = network.addHost("vehNavigation", ip="10.0.0.13/24")
        video = network.addHost("vehVideo", ip="10.0.0.14/24")
        edge = network.addHost("edge", ip="10.0.0.100/24")
        rsu = network.addSwitch("rsu", protocols="OpenFlow13", failMode="secure")
        low = network.addSwitch("low", protocols="OpenFlow13", failMode="secure")
        high = network.addSwitch("high", protocols="OpenFlow13", failMode="secure")
        edge_switch = network.addSwitch("edgeSwitch", protocols="OpenFlow13", failMode="secure")

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

    def _apply_plan(self, plan: CandidatePlan, rsu, ports: _Ports) -> None:
        """根据预览模板应用受限的流表覆盖和 OVS QoS，绝不读取外部命令。"""

        if plan.plan_id in {"critical_priority", "combined"}:
            self._configure_qos(rsu, ports.low_interface, queue_id=1, min_rate=12, max_rate=20)
            _add_flow(
                rsu,
                "ip,nw_dst=10.0.0.100,udp,tp_dst=5001",
                f"set_queue:1,output:{ports.rsu_to_low}",
                priority=200,
            )
        if plan.plan_id in {"congestion_relief", "combined"}:
            self._configure_qos(rsu, ports.high_interface, queue_id=2, min_rate=1, max_rate=8)
            _add_flow(
                rsu,
                "ip,nw_dst=10.0.0.100,udp,tp_dst=5004",
                f"set_queue:2,output:{ports.rsu_to_high}",
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
    ) -> None:
        """以 Linux HTB 创建命名队列；接口名称和费率均来自内部固定模板。"""

        command = (
            "ovs-vsctl "
            f"-- --id=@queue create Queue external_ids:owner={_OWNER} "
            f"other-config:min-rate={min_rate * 1_000_000} other-config:max-rate={max_rate * 1_000_000} "
            f"-- --id=@qos create QoS type=linux-htb external_ids:owner={_OWNER} "
            f"other-config:max-rate={max_rate * 1_000_000} queues:{queue_id}=@queue "
            f"-- set Port {interface} qos=@qos"
        )
        _run_checked(rsu, command)

    def _start_iperf_servers(self, hosts) -> None:
        """在边缘节点启动固定端口的 UDP 服务端，端口仅来自受控拓扑清单。"""

        edge = hosts["edge"]
        for port in (5001, 5002, 5003, 5004):
            _run_checked(
                edge,
                f"iperf -s -u -p {port} > /tmp/{_OWNER}-{port}.log 2>&1 &",
            )

    def _measure(self, hosts) -> TrafficMetrics:
        """在背景视频压力下采集紧急业务 P95 时延、各流量吞吐、丢包和路径利用率。"""

        video = hosts["video"]
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
        throughput = {traffic: _parse_throughput(output) for traffic, output in outputs.items()}
        loss = {traffic: _parse_loss(output) for traffic, output in outputs.items()}
        low_load = throughput["emergency"] + throughput["control"] + throughput["navigation"]
        return TrafficMetrics(
            emergency_p95_latency_ms=_p95_ping_latency(ping_output),
            throughput_mbps=throughput,
            packet_loss_percent=loss,
            link_utilization_percent={
                "low_latency": round(min(100.0, low_load / 20 * 100), 2),
                "high_capacity": round(min(100.0, throughput["video"] / 50 * 100), 2),
            },
        )

    def _clear_qos(self, rsu, ports: _Ports | None) -> None:
        """清理本次命名 QoS 与队列记录，避免在 OVSDB 中遗留孤立资源。"""

        if rsu is None or ports is None:
            return
        for interface in (ports.low_interface, ports.high_interface):
            rsu.cmd(f"ovs-vsctl --if-exists clear Port {interface} qos")
        queue_ids = rsu.cmd(
            "ovs-vsctl --data=bare --no-heading --columns=_uuid "
            f"find Queue external_ids:owner={_OWNER}"
        ).split()
        qos_ids = rsu.cmd(
            "ovs-vsctl --data=bare --no-heading --columns=_uuid "
            f"find QoS external_ids:owner={_OWNER}"
        ).split()
        if qos_ids:
            rsu.cmd(f"ovs-vsctl --if-exists destroy QoS {' '.join(qos_ids)}")
        if queue_ids:
            rsu.cmd(f"ovs-vsctl --if-exists destroy Queue {' '.join(queue_ids)}")


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


def _run_checked(node, command: str) -> str:
    """运行内部固定命令并检查 shell 返回码，拒绝静默的 OVS 或 iperf 失败。"""

    marker = "__intent_sdn_status__"
    output = node.cmd(f"{command}\nprintf '{marker}%s' $?")
    position = output.rfind(marker)
    if position < 0:
        raise RuntimeError("未取得受控命令的退出状态。")
    status = output[position + len(marker) :].strip()
    if status != "0":
        raise RuntimeError("受控网络命令执行失败。")
    return output[:position]


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
