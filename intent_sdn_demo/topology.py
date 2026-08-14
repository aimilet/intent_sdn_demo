"""拓扑清单：集中维护第一版允许引用的车辆、路径、队列和业务端口。"""

from __future__ import annotations

from dataclasses import dataclass

from intent_sdn_demo.models import TrafficClass


@dataclass(frozen=True)
class TrafficProfile:
    """单类流量的固定主机、协议端口与业务说明。"""

    vehicle_id: str
    source_ip: str
    udp_port: int
    description: str


@dataclass(frozen=True)
class TopologyInventory:
    """策略校验和编译共享的不可变网络资源清单。"""

    traffic_profiles: dict[TrafficClass, TrafficProfile]
    plan_ids: frozenset[str]
    resources: frozenset[str]

    @property
    def vehicle_ids(self) -> frozenset[str]:
        """返回允许外部意图引用的车辆集合。"""

        return frozenset(profile.vehicle_id for profile in self.traffic_profiles.values())

    def to_dict(self) -> dict[str, object]:
        """为页面提供无需敏感信息的拓扑摘要。"""

        profiles = {
            traffic_class.value: {
                "vehicle_id": profile.vehicle_id,
                "source_ip": profile.source_ip,
                "udp_port": profile.udp_port,
                "description": profile.description,
            }
            for traffic_class, profile in self.traffic_profiles.items()
        }
        return {
            "traffic_profiles": profiles,
            "paths": {
                "low_latency": {"bandwidth_mbps": 20, "delay_ms": 5},
                "high_capacity": {"bandwidth_mbps": 50, "delay_ms": 15},
            },
        }


def default_topology() -> TopologyInventory:
    """构造第一版固定拓扑，避免由用户输入指定任何网络资源。"""

    profiles = {
        TrafficClass.EMERGENCY: TrafficProfile(
            vehicle_id="veh-emergency-01",
            source_ip="10.0.0.11",
            udp_port=5001,
            description="救护车紧急 V2X 消息",
        ),
        TrafficClass.CONTROL: TrafficProfile(
            vehicle_id="veh-control-02",
            source_ip="10.0.0.12",
            udp_port=5002,
            description="车辆控制消息",
        ),
        TrafficClass.NAVIGATION: TrafficProfile(
            vehicle_id="veh-navigation-03",
            source_ip="10.0.0.13",
            udp_port=5003,
            description="导航与状态上报消息",
        ),
        TrafficClass.VIDEO: TrafficProfile(
            vehicle_id="veh-video-04",
            source_ip="10.0.0.14",
            udp_port=5004,
            description="背景视频流量",
        ),
    }
    return TopologyInventory(
        traffic_profiles=profiles,
        plan_ids=frozenset({"baseline", "critical_priority", "congestion_relief", "combined"}),
        resources=frozenset({"rsu", "low-latency-path", "high-capacity-path", "edge"}),
    )
