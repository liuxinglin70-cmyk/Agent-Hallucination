# Agent Hallucination

结果性幻觉检测器及受控自进化框架。检测器判断 Agent 轨迹中的交付错误并定位责任步骤；进化框架诊断稳定失败、开发干预，经配对评估和四门准入后发布版本。

## 📚 文档

- [核心技术文档](docs/technical-solution.md)：系统模型、模块职责、合同、审查结论和维护规则。
- [实验日志](docs/experiment-log.md)：历史实验、数值、失败、更正、旧方案及来源摘要。

历史真实实验尚未证明自进化带来稳定收益。离线测试验证程序行为，不代表模型准确率或独立泛化。

## 🚀 使用

Python 3.10 及以上，在仓库根目录执行：

```bash
python -m pip install -e ".[test]"
python -m detector --help
python -m evolution --help
python -m experiments --help
python scripts/verify.py
```

真实检测需要本地模型服务凭据，会产生模型调用：

```bash
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json --case-id BFCL/001.json --output-dir artifacts/detector/single
python -m evolution preview --cycle 1 --mini
python -m evolution experiment --mini
```

默认配置为 detector/configs/autodl.json，凭据读取 AUTODL_API_KEY，.env 不入 Git。数据放在 datasets/raw/agenthallu/AgentHallu，由 datasets/integrity.json 校验。本地完整数据有 693 个案例，默认协议是开发复测，不是独立终审。

## 🗂️ 结构

```text
detector/     检测、证据、模型调用、结果合同及测试
evolution/    开发、门控、晋升、版本、记忆及测试
experiments/  冻结计划、运行和报告
scripts/      验证、预检、研究轮和测量工具
tests/        跨模块与入口回归
datasets/     原始数据完整性、划分与暴露协议
docs/         核心技术文档、唯一实验日志
artifacts/    本地输出和缓存，不入 Git
```

依赖方向：evolution 和 experiments 使用 detector；detector 不依赖 evolution。新输出统一写 artifacts，长期结论归入实验日志。旧 reports/output 和分散文档从版本库移除；必要回放材料作为测试夹具保留。
