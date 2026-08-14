"""执行器辅助逻辑测试：验证指标解析与权限边界，不创建真实 Mininet 拓扑。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import TrafficMetrics
from intent_sdn_demo.execution import (
    MininetExecutor,
    _CreatedPolicyResources,
    _Ports,
    _HOST_NAMES,
    _SWITCH_DPIDS,
    _add_meter,
    _owned_resource_ids,
    _path_utilization_percent,
    _parse_loss,
    _parse_throughput,
    _p95_ping_latency,
    _read_port_tx_bytes,
    _run_checked,
)


class ExecutionHelperTest(unittest.TestCase):
    """覆盖不依赖 root、OVS 或网络命名空间的执行器确定性逻辑。"""

    def test_parse_metrics_from_iperf_and_ping_output(self) -> None:
        """解析器应取最后一个 iperf 汇总值并使用 ping 成功样本计算 P95。"""

        ping = "\n".join(f"64 bytes from 10.0.0.100: time={index}.0 ms" for index in range(1, 21))
        iperf = "0.0-1.0 sec  1.00 MBytes  8.00 Mbits/sec (2.0%)\n0.0-2.0 sec  2.00 MBytes  6.50 Mbits/sec (3.5%)"

        self.assertEqual(_p95_ping_latency(ping), 19.0)
        self.assertEqual(_parse_throughput(iperf), 6.5)
        self.assertEqual(_parse_loss(iperf), 3.5)

    def test_environment_requires_root_before_topology_creation(self) -> None:
        """非 root 环境必须在导入和创建 Mininet 网络之前明确失败。"""

        with patch("intent_sdn_demo.execution.os.geteuid", return_value=1000):
            with self.assertRaisesRegex(IntentError, "root 权限"):
                MininetExecutor()._validate_environment()

    def test_static_arp_is_installed_for_every_other_fixed_host(self) -> None:
        """删除广播流表前需将固定拓扑中的邻居关系预置为静态 ARP。"""

        hosts = {
            "first": FakeHost("10.0.0.11", "00:00:00:00:00:11"),
            "second": FakeHost("10.0.0.100", "00:00:00:00:00:64"),
        }

        MininetExecutor()._install_static_arp(hosts)

        self.assertEqual(hosts["first"].arp_entries, [("10.0.0.100", "00:00:00:00:00:64")])
        self.assertEqual(hosts["second"].arp_entries, [("10.0.0.11", "00:00:00:00:00:11")])

    def test_topology_uses_explicit_unique_dpids_for_display_switch_names(self) -> None:
        """非 s数字 的展示名称必须显式携带唯一 DPID，避免 Mininet 自动推导失败。"""

        network = RecordingNetwork()

        MininetExecutor()._build_topology(network)

        self.assertEqual(
            {name: parameters["dpid"] for name, parameters in network.switches.items()},
            {
                "rsu": _SWITCH_DPIDS["rsu"],
                "low": _SWITCH_DPIDS["low"],
                "high": _SWITCH_DPIDS["high"],
                "edgeSwitch": _SWITCH_DPIDS["edge"],
            },
        )
        self.assertEqual(len(set(_SWITCH_DPIDS.values())), 4)

    def test_topology_uses_short_internal_host_names_for_veth_interfaces(self) -> None:
        """Mininet 默认以节点名生成 veth 名称，内部车辆节点必须给接口名留下足够空间。"""

        network = RecordingNetwork()

        MininetExecutor()._build_topology(network)

        self.assertEqual(network.hosts, list(_HOST_NAMES.values()))
        self.assertTrue(all(len(f"{name}-eth0") <= 15 for name in network.hosts))

    def test_checked_command_uses_one_shell_line_and_parses_status(self) -> None:
        """Mininet 会在每行结束时返回提示符，状态标记必须与命令位于同一行。"""

        node = RecordingCommandNode("结果__intent_sdn_status__0")

        output = _run_checked(node, "ovs-ofctl -O OpenFlow13 del-flows rsu")

        self.assertEqual(output, "结果")
        self.assertNotIn("\n", node.commands[0])
        self.assertIn("; status=$?; printf", node.commands[0])

    def test_checked_background_command_uses_valid_async_shell_form(self) -> None:
        """后台服务命令由检查器追加异步分隔符，调用方不再拼接可能无效的 &;。"""

        node = RecordingCommandNode("__intent_sdn_status__0")

        _run_checked(node, "iperf -s -u -p 5001", background=True)

        self.assertIn("iperf -s -u -p 5001 & status=$?;", node.commands[0])

    def test_checked_command_keeps_fixed_command_diagnostic_on_failure(self) -> None:
        """受控命令失败时应保留其输出，便于定位 OVS 或 Mininet 的具体错误。"""

        node = RecordingCommandNode("OFPT_ERROR: bad meter__intent_sdn_status__1")

        with self.assertRaisesRegex(RuntimeError, "OFPT_ERROR: bad meter"):
            _run_checked(node, "ovs-ofctl -O OpenFlow13 del-meters rsu meter=2")

    def test_port_tx_bytes_and_utilization_are_calculated_from_counter_deltas(self) -> None:
        """路径利用率应取 RSU 出口实际字节增量，不能依据业务类型推断所属路径。"""

        switch = NamedCommandNode(
            "rsu",
            "port  5: rx pkts=1, bytes=64, drop=0\n"
            "         tx pkts=20, bytes=2500000, drop=0\n"
            "__intent_sdn_status__0",
        )

        tx_bytes = _read_port_tx_bytes(switch, 5)
        utilization = _path_utilization_percent(
            {"low_latency": 0, "high_capacity": 0},
            {"low_latency": tx_bytes, "high_capacity": 1_250_000},
            1.0,
        )

        self.assertEqual(tx_bytes, 2_500_000)
        self.assertEqual(utilization, {"low_latency": 100.0, "high_capacity": 20.0})
        self.assertIn("dump-ports rsu 5", switch.commands[0])

    def test_owned_resource_ids_accept_only_ovs_uuids(self) -> None:
        """清理命令只允许已验证的 OVS UUID，防止数据库输出意外进入命令参数。"""

        valid = "12345678-1234-1234-1234-1234567890ab"
        node = RecordingCommandNode(f"{valid}\n__intent_sdn_status__0")

        self.assertEqual(_owned_resource_ids(node, "Queue"), (valid,))

        invalid = RecordingCommandNode("not-a-uuid__intent_sdn_status__0")
        with self.assertRaisesRegex(RuntimeError, "不合法"):
            _owned_resource_ids(invalid, "Queue")

    def test_qos_cleanup_removes_only_owned_resources_and_verifies_absence(self) -> None:
        """清理需删除计量器、解绑端口、销毁带 owner 标记的记录并确认资源为空。"""

        queue_id = "12345678-1234-1234-1234-1234567890ab"
        qos_id = "abcdefab-cdef-cdef-cdef-abcdefabcdef"
        rsu = QoSCleanupNode(queue_id, qos_id)
        ports = _sample_ports()

        MininetExecutor()._clear_qos(rsu, ports, video_meter_id=2)

        commands = "\n".join(rsu.commands)
        self.assertIn("del-meters rsu meter=2", commands)
        self.assertIn("clear Port rsu-eth1 qos", commands)
        self.assertIn("clear Port rsu-eth2 qos", commands)
        self.assertIn(f"destroy QoS {qos_id}", commands)
        self.assertIn(f"destroy Queue {queue_id}", commands)
        self.assertEqual(rsu.queue_queries, 2)
        self.assertEqual(rsu.qos_queries, 2)

    def test_video_meter_uses_fixed_openflow13_drop_rate(self) -> None:
        """背景视频的硬限速必须通过固定 OpenFlow 计量器执行，不能受外部输入影响。"""

        rsu = NamedCommandNode("rsu", "__intent_sdn_status__0")

        _add_meter(rsu, 2, 8.0)

        self.assertIn("-O OpenFlow13 add-meter rsu", rsu.commands[0])
        self.assertIn(
            "meter=2,kbps,burst,band=type=drop,rate=8000,burst_size=64",
            rsu.commands[0],
        )

    def test_congestion_metric_validation_rejects_unmet_video_rate(self) -> None:
        """模板承诺视频不超过 8 Mbps 时，超出容差的实测结果必须明确失败。"""

        metrics = TrafficMetrics(
            emergency_p95_latency_ms=10.0,
            throughput_mbps={"video": 18.9},
            packet_loss_percent={"video": 0.0},
            link_utilization_percent={"low_latency": 0.0, "high_capacity": 20.0},
        )

        with self.assertRaisesRegex(IntentError, "背景视频限速未达到"):
            MininetExecutor()._validate_applied_metrics(
                type("Plan", (), {"plan_id": "combined"})(), metrics
            )

    def test_video_meter_is_recorded_before_following_flow_failure(self) -> None:
        """计量器创建后即使随后流表下发失败，finally 也必须知道需要删除该计量器。"""

        executor = MininetExecutor()
        ports = _sample_ports()
        resources = _CreatedPolicyResources()
        plan = type("Plan", (), {"plan_id": "congestion_relief"})()

        with (
            patch.object(executor, "_configure_qos"),
            patch("intent_sdn_demo.execution._add_meter"),
            patch("intent_sdn_demo.execution._add_flow", side_effect=RuntimeError("流表失败")),
        ):
            with self.assertRaisesRegex(RuntimeError, "流表失败"):
                executor._apply_plan(plan, NamedCommandNode("rsu", ""), ports, resources)

        self.assertEqual(resources.video_meter_id, 2)


def _sample_ports() -> _Ports:
    """构造固定端口样本，供不创建真实 Mininet 的策略下发测试复用。"""

    return _Ports(
        rsu_to_low=1,
        rsu_to_high=2,
        rsu_to_emergency=3,
        rsu_to_control=4,
        rsu_to_navigation=5,
        rsu_to_video=6,
        low_to_rsu=1,
        low_to_edge_switch=2,
        high_to_rsu=1,
        high_to_edge_switch=2,
        edge_switch_to_low=1,
        edge_switch_to_high=2,
        edge_switch_to_edge=3,
        low_interface="rsu-eth1",
        high_interface="rsu-eth2",
    )


class FakeHost:
    """只模拟静态 ARP 配置需要的 Mininet Host 接口。"""

    def __init__(self, address: str, mac: str) -> None:
        """保存固定地址、MAC 和被设置的邻居记录。"""

        self._address = address
        self._mac = mac
        self.arp_entries: list[tuple[str, str]] = []

    def IP(self) -> str:  # noqa: N802
        """返回模拟主机 IPv4 地址，匹配 Mininet 的公开 API 命名。"""

        return self._address

    def MAC(self) -> str:  # noqa: N802
        """返回模拟主机 MAC 地址，匹配 Mininet 的公开 API 命名。"""

        return self._mac

    def setARP(self, address: str, mac: str) -> None:  # noqa: N802
        """记录静态邻居关系，匹配 Mininet 的公开 API 命名。"""

        self.arp_entries.append((address, mac))


class RecordingNetwork:
    """记录拓扑构造调用，避免测试为了 DPID 断言而创建真实网络命名空间。"""

    def __init__(self) -> None:
        """初始化受控节点和交换机参数记录。"""

        self.switches: dict[str, dict[str, object]] = {}
        self.hosts: list[str] = []

    def addHost(self, name: str, **_parameters):  # noqa: N802
        """返回可被 addLink 引用的轻量主机占位对象。"""

        self.hosts.append(name)
        return name

    def addSwitch(self, name: str, **parameters):  # noqa: N802
        """记录构造器收到的 DPID 和 OpenFlow 参数。"""

        self.switches[name] = parameters
        return name

    def addLink(self, *_nodes, **_parameters):  # noqa: N802
        """接受固定链路定义；本测试不需要真实 Link 对象。"""

        return object()


class RecordingCommandNode:
    """模拟 Mininet 节点命令输出，用于验证单行状态检查脚本。"""

    def __init__(self, output: str) -> None:
        """保存固定输出和执行脚本记录。"""

        self._output = output
        self.commands: list[str] = []

    def cmd(self, command: str) -> str:
        """记录脚本并返回预置的 Mininet 命令输出。"""

        self.commands.append(command)
        return self._output


class NamedCommandNode(RecordingCommandNode):
    """为端口统计测试额外提供交换机名称的轻量节点。"""

    def __init__(self, name: str, output: str) -> None:
        """保存固定节点名并复用命令输出记录功能。"""

        super().__init__(output)
        self.name = name


class QoSCleanupNode:
    """按清理顺序模拟 OVSDB 查询结果，验证不会留下本次命名的资源。"""

    def __init__(self, queue_id: str, qos_id: str) -> None:
        """保存首轮发现的资源 UUID 和命令记录。"""

        self.name = "rsu"
        self._queue_id = queue_id
        self._qos_id = qos_id
        self.queue_queries = 0
        self.qos_queries = 0
        self.commands: list[str] = []

    def cmd(self, command: str) -> str:
        """模拟清理前有资源、销毁后为空的 OVS 命令输出。"""

        self.commands.append(command)
        if "find Queue" in command:
            self.queue_queries += 1
            output = self._queue_id if self.queue_queries == 1 else ""
        elif "find QoS" in command:
            self.qos_queries += 1
            output = self._qos_id if self.qos_queries == 1 else ""
        else:
            output = ""
        return f"{output}__intent_sdn_status__0"
