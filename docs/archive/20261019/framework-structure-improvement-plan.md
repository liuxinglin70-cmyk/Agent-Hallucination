# 检测器框架代码结构评估与完善计划

> 历史材料归档（2026-10-19）：保留当时分析、预期及修正记录，不代表当前实现或验收结论。当前结论见[实验日志](../../experiment-log.md)。

**日期**: 2026-10-16  
**目标**: 评估当前框架代码质量，识别结构性改进点  
**范围**: 代码架构、模块设计、可维护性、扩展性

---

## 📊 当前框架状态评估

### 1. 核心结构评估

#### 1.1 模块组织
```
detector/
├── api.py              # 主检测接口
├── packet.py           # 输入数据标准化
├── judge.py            # 判决投票
├── recaller.py         # 证据召回与升级
├── step_tracer.py      # 来源定位
├── action_forecast.py  # 下一步预测（新增）
├── action_evidence.py  # 动作证据（新增）
├── replay.py           # 回放身份管理
├── provider.py         # 模型调用
├── runtime.py          # 运行时资源
└── tests/              # 测试套件
```

**评估**：✅ 模块职责清晰，边界明确

#### 1.2 代码质量指标
```bash
总文件数: 110个
测试覆盖: 1252个测试
测试通过率: 100%
```

**评估**：✅ 测试覆盖充分

---

## 🔍 需要完善的结构性问题

### 问题1: Schema版本管理不统一

**现状**：
```python
# action_forecast.py
FORECAST_SCHEMA = "detector.action_forecast.v2"

# packet.py
PACKET_SCHEMA = "detector.packet.v1"

# replay.py
REPLAY_INPUTS_SCHEMA = 1
```

**问题**：
- Schema版本格式不一致（字符串 vs 整数）
- 缺少统一的版本升级机制
- 难以追踪不同版本的兼容性

**影响**: 中等（影响数据持久化和回放兼容性）

---

### 问题2: 错误处理不够细化

**现状**：
```python
# 多处使用通用异常
raise ValueError("invalid input")
raise RuntimeError("unexpected state")
```

**问题**：
- 缺少自定义异常类型
- 调用方难以精确捕获和处理
- 错误信息不够结构化

**影响**: 低（功能正常，但调试效率低）

---

### 问题3: 配置验证分散

**现状**：
```python
# 配置验证逻辑分散在各个模块
def run_judge(..., config):
    route = config["judge_route"]  # 可能KeyError
    votes = route.get("votes", 1)  # 默认值不明确
```

**问题**：
- 缺少集中的配置schema定义
- 验证逻辑分散，难以维护
- 默认值散布在代码各处

**影响**: 中等（配置错误难以提前发现）

---

### 问题4: 日志和监控不够完善

**现状**：
```python
# 缺少结构化日志
# 缺少关键决策点的日志记录
# 缺少性能监控埋点
```

**问题**：
- 难以追踪复杂案例的执行路径
- 性能瓶颈不易定位
- 生产问题难以排查

**影响**: 中等（影响可观测性）

---

### 问题5: Phase 1诊断字段的定位模糊

**现状**：
```python
# action_forecast.py
row["failure_detail"] = "model_did_not_provide_comparison"

# recaller.py
result_row["upgrade_rejection"] = rejection_detail
```

**问题**：
- 这些字段是用于开发调试还是生产监控？
- 如果是调试用，应该有开关控制
- 如果是生产用，应该有文档说明用途

**影响**: 低（功能正常，但目的不清晰）

---

## ✅ 推荐的完善计划

### 优先级P0: 关键结构性改进

#### 任务1: 统一Schema版本管理
**目标**: 建立统一的版本管理机制

**实施**：
```python
# 新建 detector/schema.py
class SchemaVersion:
    """统一的Schema版本管理"""
    
    # 所有schema使用统一格式
    PACKET = "detector.packet.v1"
    FORECAST = "detector.action_forecast.v2"
    REPLAY_INPUTS = "detector.replay_inputs.v1"
    
    @staticmethod
    def validate(data, expected_schema):
        """验证数据schema版本"""
        actual = data.get("schema_version")
        if actual != expected_schema:
            raise SchemaVersionError(f"Expected {expected_schema}, got {actual}")
    
    @staticmethod
    def supports_upgrade(from_version, to_version):
        """检查版本升级路径"""
        # 实现版本兼容性逻辑
        pass
```

**收益**：
- 统一版本管理
- 清晰的升级路径
- 便于回放兼容性检查

---

#### 任务2: 建立配置Schema与验证
**目标**: 集中管理配置定义和验证

**实施**：
```python
# 新建 detector/config_schema.py
from typing import TypedDict, Literal

class RouteConfig(TypedDict):
    endpoint: str
    model: str
    api_key_env: str
    temperature: float
    timeout_seconds: int
    votes: int  # 明确必需字段

class DetectorConfig(TypedDict):
    judge_route: RouteConfig
    recall_route: RouteConfig
    defense_route: RouteConfig
    causal_audit: dict
    # ... 其他字段

def validate_config(config: dict) -> DetectorConfig:
    """验证并返回类型化配置"""
    # 使用pydantic或自定义验证
    # 检查必需字段
    # 检查类型正确性
    # 应用默认值
    return validated_config
```

**收益**：
- 配置错误提前发现
- IDE类型提示支持
- 文档自动生成

---

### 优先级P1: 可观测性改进

#### 任务3: 添加结构化日志
**目标**: 关键决策点有清晰的日志记录

**实施**：
```python
# 在关键函数中添加结构化日志
import logging
from detector.logging_utils import log_decision

def run_judge(...):
    log_decision("judge_start", {
        "packet_digest": digest(packet),
        "votes": votes,
        "mode": "with_forecast" if forecast else "baseline"
    })
    
    # ... 执行逻辑 ...
    
    log_decision("judge_complete", {
        "decision": result["decision"],
        "authority": result["decision_authority"],
        "duration_ms": elapsed_time
    })
```

**关键决策点**：
- Judge投票结果
- Recall候选生成
- Upgrade决策
- Tracer定位结果
- 预测生成与比较

**收益**：
- 复杂案例可追踪
- 性能瓶颈可定位
- 生产问题可排查

---

#### 任务4: 添加性能监控埋点
**目标**: 关键路径的性能可测量

**实施**：
```python
# 使用装饰器或上下文管理器
from detector.metrics import track_performance

@track_performance("judge_voting")
def run_judge(...):
    pass

# 或者
with track_performance("forecast_prediction"):
    forecast = build_action_forecast(...)
```

**监控指标**：
- 各阶段执行时间
- 模型调用次数和耗时
- 缓存命中率
- 内存使用峰值

**收益**：
- 性能回归及时发现
- 瓶颈环节清晰
- 优化效果可量化

---

### 优先级P2: 代码质量提升

#### 任务5: 引入自定义异常类型
**目标**: 错误处理更精确

**实施**：
```python
# 新建 detector/exceptions.py
class DetectorError(Exception):
    """检测器基础异常"""
    pass

class SchemaVersionError(DetectorError):
    """Schema版本不匹配"""
    pass

class ConfigurationError(DetectorError):
    """配置错误"""
    pass

class ProviderError(DetectorError):
    """模型调用错误（已存在）"""
    pass

class ContractViolationError(DetectorError):
    """输出合同违反"""
    pass
```

**使用**：
```python
# 替换通用异常
raise ConfigurationError(f"Missing required field: {field}")

# 调用方精确捕获
try:
    result = detect(case, config)
except ConfigurationError as e:
    # 配置错误，修正配置
    pass
except ProviderError as e:
    # 模型错误，重试或降级
    pass
```

**收益**：
- 错误处理更精确
- 调用方可针对性处理
- 错误分类更清晰

---

#### 任务6: 完善文档字符串
**目标**: 所有公共API有清晰文档

**实施**：
```python
def detect(record: dict, config: dict, **kwargs) -> dict:
    """检测轨迹中的agent幻觉。
    
    Args:
        record: 原始轨迹记录，包含question和history
        config: 检测配置，必须包含judge_route等路由配置
        frozen_stages: 可选，冻结的阶段结果用于回放
        require_credentials: 是否要求模型凭证，默认True
    
    Returns:
        检测结果字典，包含：
        - state: 检测状态（verified_error/verified_clean/...）
        - decision_authority: 判决权威性
        - responsible_step: 定位的错误步骤（如果有）
        - proof: 完整证明链
    
    Raises:
        ConfigurationError: 配置无效
        ProviderError: 模型调用失败且不可恢复
    
    Example:
        >>> result = detect(
        ...     record={"question": "...", "history": [...]},
        ...     config=load_config()
        ... )
        >>> print(result["state"])
        verified_error
    """
    pass
```

**覆盖范围**：
- 所有公共函数
- 关键数据结构
- 重要的内部函数

**收益**：
- 降低使用门槛
- IDE提示更友好
- 自动生成API文档

---

### 优先级P3: 可选的增强

#### 任务7: Phase 1诊断字段的用途明确化
**目标**: 明确诊断字段的定位和使用方式

**选项A: 作为开发调试工具**
```python
# 添加配置开关
config["diagnostic_mode"] = True  # 开启详细诊断

if config.get("diagnostic_mode"):
    row["failure_detail"] = ...
    result_row["upgrade_rejection"] = ...
```

**选项B: 作为生产监控指标**
```python
# 保留字段，添加文档说明用途
"""
failure_detail字段用于监控系统，记录比较失败的具体原因，
可用于：
1. 告警规则（如quote_missing占比异常）
2. 趋势分析（失败类型分布变化）
3. 问题排查（定位具体失败原因）
"""
```

**选项C: 移除或简化**
```python
# 如果不确定用途，暂时移除
# 等明确需求后再添加
```

**建议**: 选择选项B，保留字段但明确文档说明

---

#### 任务8: 添加配置预设
**目标**: 常见场景的配置模板

**实施**：
```python
# detector/config_presets.py
def get_config_preset(preset_name: str) -> dict:
    """获取预设配置"""
    presets = {
        "development": {
            # 开发环境：更多日志，更多诊断
            "diagnostic_mode": True,
            "log_level": "DEBUG",
            "causal_audit": {"next_action_mode": "shadow"},
        },
        "production": {
            # 生产环境：精简日志，完整功能
            "diagnostic_mode": False,
            "log_level": "INFO",
            "causal_audit": {"next_action_mode": "judge_context"},
        },
        "testing": {
            # 测试环境：确定性行为
            "temperature": 0.0,
            "causal_audit": {"next_action_mode": "off"},
        },
    }
    return presets[preset_name]
```

**收益**：
- 降低配置门槛
- 最佳实践内置
- 环境切换方便

---

## 📋 实施路线图

### 第一阶段（1-2天，P0任务）
**目标**: 解决关键结构性问题

- [ ] 任务1: 统一Schema版本管理
- [ ] 任务2: 建立配置Schema与验证

**交付物**:
- `detector/schema.py` - 统一版本管理
- `detector/config_schema.py` - 配置验证
- 更新现有代码使用新schema
- 测试验证

---

### 第二阶段（2-3天，P1任务）
**目标**: 提升可观测性

- [ ] 任务3: 添加结构化日志
- [ ] 任务4: 添加性能监控埋点

**交付物**:
- `detector/logging_utils.py` - 日志工具
- `detector/metrics.py` - 性能监控
- 在关键路径添加埋点
- 测试验证

---

### 第三阶段（1-2天，P2任务）
**目标**: 代码质量提升

- [ ] 任务5: 引入自定义异常类型
- [ ] 任务6: 完善文档字符串

**交付物**:
- `detector/exceptions.py` - 异常类型
- 更新所有公共API文档
- 更新错误处理逻辑
- 文档验证

---

### 第四阶段（可选，P3任务）
**目标**: 锦上添花

- [ ] 任务7: Phase 1诊断字段用途明确
- [ ] 任务8: 添加配置预设

**交付物**:
- 诊断字段文档
- `detector/config_presets.py` - 配置模板
- 使用示例

---

## ✅ 成功标准

### 代码质量
- [ ] 所有公共API有完整文档
- [ ] 配置错误可提前发现
- [ ] 异常类型清晰可捕获
- [ ] Schema版本统一管理

### 可观测性
- [ ] 关键决策点有日志
- [ ] 性能指标可测量
- [ ] 复杂案例可追踪
- [ ] 问题排查有数据

### 可维护性
- [ ] 新人可快速理解代码
- [ ] 配置错误有清晰提示
- [ ] 模块职责边界清晰
- [ ] 测试覆盖保持100%

---

## 🎯 当前建议

基于评估，我建议按以下优先级实施：

### 立即开始（推荐）
**任务1 + 任务2**: 统一Schema版本管理 + 配置验证
- 影响: 中高
- 难度: 中
- 耗时: 1-2天
- 收益: 提升系统稳定性和可维护性

### 其次考虑
**任务3 + 任务4**: 结构化日志 + 性能监控
- 影响: 中
- 难度: 中
- 耗时: 2-3天
- 收益: 提升可观测性，便于后续优化和问题排查

### 最后完善
**任务5 + 任务6**: 自定义异常 + 文档完善
- 影响: 低中
- 难度: 低
- 耗时: 1-2天
- 收益: 提升代码质量和易用性

---

**评估人**: Claude Opus 5.5  
**评估日期**: 2026-10-16  
**状态**: 待确认实施计划
