"""执行器辅助逻辑测试：验证指标解析与权限边界，不创建真实 Mininet 拓扑。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.execution import (
    MininetExecutor,
    _SWITCH_DPIDS,
    _parse_loss,
    _parse_throughput,
    _p95_ping_latency,
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

    def addHost(self, name: str, **_parameters):  # noqa: N802
        """返回可被 addLink 引用的轻量主机占位对象。"""

        return name

    def addSwitch(self, name: str, **parameters):  # noqa: N802
        """记录构造器收到的 DPID 和 OpenFlow 参数。"""

        self.switches[name] = parameters
        return name

    def addLink(self, *_nodes, **_parameters):  # noqa: N802
        """接受固定链路定义；本测试不需要真实 Link 对象。"""

        return object()
