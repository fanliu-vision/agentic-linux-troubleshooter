# Enterprise Order Monitoring Service

这是一个用于验证 Stage 4C 的企业级 Demo 项目。

它模拟一个订单风控监控服务，包含多种混合问题：

1. 主故障：metrics exporter 端口 9100 冲突，导致服务启动失败；
2. 次要问题：Python 解释器与 pip 环境不一致；
3. 次要问题：内部 SDK 缺失，服务降级到本地规则；
4. 次要问题：缓存目录写入失败，fallback 到内存缓存。

## 初始运行

```bash
python run_service.py --config config.json