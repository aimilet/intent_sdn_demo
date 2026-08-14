# Mininet 集成验收记录

## 2026-08-14：`combined` 初次实测

在具备 root 权限的本机，通过页面完成“综合保障”策略的解析、编译、人工确认下发和指标展示。实测结果如下：

| 指标 | 基线 | 策略后 |
|---|---:|---:|
| 紧急业务 P95 时延 | 24.7 ms | 19.9 ms |
| emergency 吞吐 | 4.45 Mbps | 5.25 Mbps |
| control 吞吐 | 2.80 Mbps | 3.16 Mbps |
| navigation 吞吐 | 2.74 Mbps | 2.11 Mbps |
| video 吞吐 | 17.20 Mbps | 7.78 Mbps |
| emergency 丢包 | 0% | 0% |
| control 丢包 | 1.5% | 0% |
| navigation 丢包 | 9.2% | 0% |
| video 丢包 | 1.3% | 50% |

该结果确认了临时 Mininet 拓扑、OpenFlow 转发和 OVS QoS 队列能够成功运行：`combined` 将视频压至接近 8 Mbps 的模板上限，同时改善了紧急业务的时延和吞吐。视频丢包上升是 18 Mbps 背景发送速率被固定限至 8 Mbps 左右的预期结果。

## 2026-08-14：端口计数与重置复测

同一页面再次运行 `combined`，并成功调用重置接口（两次 HTTP 状态均为 200）。实测结果如下：

| 指标 | 基线 | 策略后 |
|---|---:|---:|
| 紧急业务 P95 时延 | 24.4 ms | 20.9 ms |
| emergency 吞吐 | 4.24 Mbps | 5.25 Mbps |
| control 吞吐 | 2.51 Mbps | 3.16 Mbps |
| navigation 吞吐 | 2.90 Mbps | 2.11 Mbps |
| video 吞吐 | 17.00 Mbps | 18.90 Mbps |
| emergency 丢包 | 0% | 0% |
| control 丢包 | 16.0% | 0% |
| navigation 丢包 | 3.6% | 0% |
| video 丢包 | 2.4% | 0% |
| 低时延路径利用率 | 73.85% | 11.55% |
| 高容量路径利用率 | 0% | 14.60% |

该结果确认 RSU 端口计数反映出了视频由低时延路径迁移至高容量路径，且确认下发与重置的闭环可用。但是视频策略后吞吐为 18.90 Mbps，违反 `combined` 的 8 Mbps 模板上限，因此本次**不能**作为完整策略验收通过的证据。

## 限速修复后的最终复测要求

原因是 OVS Queue 在此 TCLink/OVS 组合中完成了 `set_queue` 选队和流量迁移，却没有可靠地施加整形。当前实现保留队列，并额外为视频流下发 OpenFlow 1.3 丢弃型计量器（8000 kbps）；若实测视频吞吐超过 8.5 Mbps，服务将返回 `policy_effectiveness_failed`，而不是报告成功。临时拓扑结束时还会删除已创建的计量器并查询带 `intent-sdn-demo` 标记的 QoS/Queue 是否清空。

请以 root 权限启动服务后重新运行一次 `combined` 并点击“重置”。验收要求为：视频吞吐不高于 8.5 Mbps、视频流量主要位于高容量路径、接口返回 200。该次复测成功后才可关闭 P7-03 和 P7-04。
