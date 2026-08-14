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

## 最终复测要求

初次运行的“路径利用率”按业务类型推算，不能作为验收证据。当前实现已改为在每个测量窗口前后读取 RSU 两个核心出口的 OVS TX 字节计数，并据此计算平均利用率；同时会验证带 `intent-sdn-demo` owner 标记的 QoS 和 Queue 已被清空。

因此，发布当前版本后需在同一页面重新运行一次 `combined` 并点击“重置”。只有这次复测成功，才能关闭 P5-03、P5-04、P6-02 和 P7-03。
