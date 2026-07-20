# ComfyUI 架构文档（中文）

> 基于 ComfyUI **v0.28.0** 源码梳理。所有关键点均标注 `文件:行号`，可直接跳转。
> 配套文档：[`docs/SOURCE_DIVE_CN.md`](docs/SOURCE_DIVE_CN.md)（三大子系统源码导读）、[`custom_nodes/example_tutorial/`](custom_nodes/example_tutorial/)（教学自定义节点）。

---

## 1. 项目定位与技术栈

ComfyUI 是目前最主流的**模块化 AI 内容生成引擎**，用节点图（node graph）编排图像 / 视频 / 音频 / 3D 等生成工作流。核心设计理念：

- **节点化**：一切皆节点，工作流 = 有向无环图（DAG）。
- **本地优先**：core 永不主动联网（`AGENTS.md` 明确禁止任何遥测/上报）。
- **智能显存**：在 1GB VRAM 上也能跑大模型（权重按需在 CPU/GPU 间搬运）。
- **增量执行**：只重算"输入发生了变化"的子图，其余节点复用缓存结果。

| 维度 | 选型 |
|---|---|
| 语言 | Python ≥ 3.10（推荐 3.12 / 3.13） |
| 推理框架 | PyTorch（CUDA / ROCm / MPS / DirectML / XPU / NPU） |
| Web 服务 | aiohttp（异步 HTTP + WebSocket） |
| 数据库 | SQLite + SQLAlchemy 2.x + Alembic（资产 / 任务持久化） |
| 前端 | **独立仓库** [ComfyUI_frontend](https://github.com/Comfy-Org/ComfyUI_frontend)（TS/Vue），编译产物经 `comfyui-frontend-package` 以 pip 依赖注入 |
| 优化算子 | `comfy-kitchen`（融合算子）、`comfy-aimdo`（DynamicVRAM 动态显存，需 PyTorch ≥ 2.8） |

---

## 2. 顶层目录地图

```
ComfyUI/
├── main.py              # 启动入口：解析参数 → 初始化 → 起 HTTP/WS 服务 + 执行线程
├── server.py            # PromptServer：所有 HTTP/WebSocket 路由（~1300 行）
├── execution.py         # 执行引擎：PromptExecutor、PromptQueue、缓存、图执行（~1400 行）
├── nodes.py             # 内置核心节点 + 所有节点的统一加载入口（~2600 行）
├── folder_paths.py      # 模型 / 输入 / 输出 / 临时目录的路径解析中心
├── comfy/               # ★ 核心库：模型管理、采样器、模型实现(ldm/)
├── comfy_execution/     # ★ 图执行底层：graph / caching / validation / jobs / progress
├── comfy_extras/        # ★ 内置扩展节点（上百个 nodes_*.py）
├── comfy_api/           # ★ 版本化公共 API 抽象层（v0_0_1 / v0_0_2 / latest）
├── comfy_api_nodes/     # ★ 云端 API 节点（OpenAI / Gemini / Kling / Runway … 各厂商）
├── api_server/          # REST API 服务层（新版 internal 路由）
├── app/                 # 应用层服务（用户 / 模型文件 / 子图 / 资产 / DB / 前端管理）
├── custom_nodes/        # ★ 用户自定义节点目录（自动加载，含官方示例）
├── alembic_db/          # 数据库迁移脚本
├── blueprints/          # 预置工作流模板（JSON）
├── models/ input/ output/  # 默认模型 / 输入 / 输出目录
└── tests/ tests-unit/   # 测试
```

---

## 3. 分层架构与请求主链路

```
┌───────────────────────────────────────────────────────────────┐
│  Frontend（独立 Vue 项目，经 pip 注入）  ←→  WebSocket + HTTP   │
└────────────────────────────┬──────────────────────────────────┘
                             │  POST /prompt（工作流 JSON）
┌────────────────────────────▼──────────────────────────────────┐
│  server.py  PromptServer (aiohttp)                             │
│  路由：/prompt /queue /history /ws /object_info /api/jobs …    │
│  校验：execution.validate_prompt                               │
└────────────────────────────┬──────────────────────────────────┘
                             │  入队（PromptQueue.put）
┌────────────────────────────▼──────────────────────────────────┐
│  PromptQueue（堆 + 条件变量）  ──→  prompt_worker 线程(main.py) │
└────────────────────────────┬──────────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────────┐
│  execution.py  PromptExecutor                                  │
│   · 构建动态图 → 批量查缓存 → 拓扑溶解逐节点执行              │
│   · 增量：缓存命中则跳过；变化节点及其下游才重算               │
│   · 每节点：收集输入 → 调 FUNCTION → 写缓存 → 推进度           │
└─────────┬──────────────────────────────────────┬──────────────┘
          │ 每个 node 的 FUNCTION 调用             │
┌─────────▼─────────┐                ┌────────────▼──────────────┐
│ comfy_execution/  │                │ comfy/ 核心库             │
│ graph/validation/ │                │ model_management(显存/设备)│
│ caching/jobs      │                │ samplers/sample(采样)     │
│                   │                │ sd/model_patcher(加载/补丁)│
│                   │                │ ldm/* (各模型网络实现)    │
└───────────────────┘                └───────────────────────────┘
```

### 一次生成的完整链路

1. 前端把工作流 JSON（节点 + 连线 + 参数）`POST /prompt`（`server.py:1063`）。
2. 服务端 `execution.validate_prompt`（`execution.py:1121`）同步校验整张图：节点存在性、连线合法性、类型匹配、必填项、范围；返回 `(valid, error, outputs_to_execute, node_errors)`。
3. 校验通过后组装 6 元组 `queue.put(...)`（`server.py:1121`，元组结构见下表）。
4. 后台 `prompt_worker` 线程（`main.py:316` / 启动于 `main.py:525`）从队列 `get` 出任务，调用 `PromptExecutor.execute`（`execution.py:724`）。
5. `execute_async`（`execution.py:727`）在 `torch.inference_mode` 下批量查缓存（发 `execution_cached` 事件），然后用 `ExecutionList` 拓扑溶解（`graph.py:193`），逐节点调 `execute`（`execution.py:436`）。
6. 节点内部调用 `comfy/`（加载模型 → 采样 → VAE 解码），进度经 `hijack_progress`（`main.py:422`）通过 WebSocket 推回前端。
7. 输出节点（`SaveImage` 等）落盘到 `output/`，结果写进 `PromptQueue.history`（`execution.py:1279`），前端轮询 `/history`（`server.py:1035`）或新版 `/api/jobs`（`server.py:811`）取回。

### 队列元组结构（`server.py:1121`，消费于 `main.py:350`）

| 位置 | 字段 | 含义 |
|---|---|---|
| `[0]` | `number` | 优先级（堆用）；`front=True` 时取负数插队 |
| `[1]` | `prompt_id` | UUID 字符串 |
| `[2]` | `prompt` | 完整图字典 `{node_id: {inputs, class_type, ...}}` |
| `[3]` | `extra_data` | client_id / extra_pnginfo 等附加数据 |
| `[4]` | `outputs_to_execute` | 要执行的输出节点 id 列表 |
| `[5]` | `sensitive` | 敏感字段（如 auth_token），**不入历史** |

---

## 4. 核心子系统

### 4.1 节点系统

**节点定义两种风格**（`custom_nodes/` 里两种都有官方示例）：

- **经典风格**（绝大多数节点用）：实现 `INPUT_TYPES` / `RETURN_TYPES` / `FUNCTION` / `CATEGORY` 类属性，文件底部导出 `NODE_CLASS_MAPPINGS` + `NODE_DISPLAY_NAME_MAPPINGS` 字典。参考 `custom_nodes/websocket_image_save.py`、`nodes.py:56`（`CLIPTextEncode`）。
- **新风格 v1**（基于版本化 API）：继承 `io.ComfyNode`，用 `define_schema()` 声明 IO，通过 `ComfyExtension` + `comfy_entrypoint()` 注册，支持 lazy input、`fingerprint_inputs` 等高级特性。参考 `custom_nodes/example_node.py.example`。

**节点加载入口**：`init_extra_nodes`（`nodes.py:2539`），统一通过 `load_custom_node`（`nodes.py:2227`）import 模块并取 `NODE_CLASS_MAPPINGS`。三种来源：

| 来源 | 加载方式 | 入口 |
|---|---|---|
| **核心节点** | 直接写在 `nodes.py` 的 `NODE_CLASS_MAPPINGS` | `nodes.py:2049` |
| **内置扩展** `comfy_extras/` | **硬编码文件列表**逐个 import（⚠️ 新增文件需登记） | `init_builtin_extra_nodes` `nodes.py:2373`，文件清单 `nodes.py:2384` |
| **云端 API 节点** `comfy_api_nodes/` | `glob("nodes_*.py")` 自动扫描 | `nodes.py:2521` |
| **自定义节点** `custom_nodes/` | 自动扫描目录 | `init_external_custom_nodes` `nodes.py:2323` |

> ⚠️ 关键：`comfy_extras` 是**手动列表**，新增一个 `nodes_xxx.py` 若不登记到 `nodes.py:2384`，不会被加载。

### 4.2 执行引擎（`execution.py` + `comfy_execution/`）

| 组件 | 位置 | 职责 |
|---|---|---|
| `PromptExecutor` | `execution.py:662` | 执行入口，`execute`（`:724`）转发到 `execute_async`（`:727`） |
| 单节点 `execute` | `execution.py:436` | 查缓存 → 取输入 → 实例化节点 → 调 `FUNCTION` → 写 `CacheEntry` |
| `validate_prompt` | `execution.py:1121` | 图校验（节点存在、环检测、类型匹配、范围、自定义校验） |
| `PromptQueue` | `execution.py:1244` | 堆队列 + history，`put`/`get`/`task_done`/`interrupt_if_running` |
| `DynamicPrompt` | `graph.py:21` | 工作流图对象 |
| `ExecutionList` | `graph.py:193` | 拓扑溶解（动态 ready 队列，支持 lazy / subgraph / async） |
| 缓存键 | `caching.py:82` | `CacheKeySetInputSignature`：整条祖先链的输入签名 |

**缓存机制**——这是"增量执行"的核心。`CacheType` 四种（`execution.py:107`，默认在 `main.py:328` 配置）：

| 类型 | 选定条件 | 实现 |
|---|---|---|
| `RAM_PRESSURE` | **默认** | 按系统可用内存压力驱逐（`caching.py:522`） |
| `CLASSIC` | `--cache-classic` | `HierarchicalCache` |
| `LRU` | `--cache-lru N` | `LRUCache(max_size=N)` |
| `NONE` | `--cache-none` | `NullCache`，完全禁用 |

缓存键 = **整条祖先链的输入签名**（`caching.py:101`）：`[class_type, IS_CHANGED 值, (输入名, 值或上游引用)...]`。任一上游输入或 `IS_CHANGED` 返回值变化 → 签名变化 → 旧缓存自动失效。命中时节点直接跳过（`execution.py:444`）。

**中断机制**：`POST /interrupt` → `nodes.interrupt_processing`（`nodes.py:51`）→ 全局标志位（`model_management.py:2020`）；每个节点切片前 `before_node_execution` 检查标志并抛 `InterruptProcessingException`（`model_management.py:2032`）。

**Jobs 新机制**：`/api/jobs`（`server.py:811`）**不是独立存储**，而是 queue + history 之上的统一视图层（`comfy_execution/jobs.py` 纯函数归一化），与老的 `/queue`+`/history` 完全共存兼容。

### 4.3 模型加载与显存管理（`comfy/`）

**Checkpoint → ModelPatcher 完整流程**（详见 `docs/SOURCE_DIVE_CN.md` §2）：

```
CheckpointLoaderSimple.load_checkpoint            nodes.py:627
  └─ folder_paths.get_full_path_or_raise           nodes.py:628
  └─ comfy.sd.load_checkpoint_guess_config         nodes.py:629 → sd.py:1864
       └─ load_state_dict_guess_config             sd.py:1931
            ├─ model_detection.model_config_from_unet   sd.py:1947  (架构识别)
            │    └─ model_config_from_unet_config       model_detection.py:1211
            │         └─ 遍历 supported_models.models   supported_models.py:2345
            ├─ model = model_config.get_model(...)      sd.py:1979
            ├─ ModelPatcher(model, load_dev, off_dev)   sd.py:1982  ← 这就是 MODEL
            ├─ model.load_model_weights(...)            sd.py:1983
            ├─ vae = VAE(sd=...)                        sd.py:1989
            └─ clip = CLIP(clip_target, ...)            sd.py:2018
```

**显存管理**（`comfy/model_management.py`）——注意：**没有 `ModelManager` 类**，真实结构是「模块级函数 + `LoadedModel` 包装类 + 全局 list `current_loaded_models`」：

- `VRAMState` 枚举（`model_management.py:43`）：`DISABLED / NO_VRAM / LOW_VRAM / NORMAL_VRAM / HIGH_VRAM / SHARED`，由 `--lowvram`/`--novram`/`--highvram`/`--gpu-only` 决定（`model_management.py:548`）。
- `current_loaded_models: list[LoadedModel]`（`:610`）——唯一的"已加载模型注册表"。
- `LoadedModel`（`:694`）：用 `weakref` 持有 `ModelPatcher`，提供 `model_load`/`model_unload`/`model_use_more_vram`。
- **主加载器 `load_models_gpu`**（`:860`）：去重 → 卸载同源 clone → 两轮 `free_memory` 腾显存 → 按 VRAMState 决定**全量加载 vs 部分加载（lowvram）**。
- `free_memory`（`:816`）：按"offload 收益最大"排序逐个 `model_unload` 直到腾够字节。

**ModelPatcher**（`comfy/model_patcher.py:292`）——这是 ComfyUI 最关键的抽象之一。它包装底层 `nn.Module`，额外承载：

- `self.patches`：LoRA 等"权重 diff"（按 key 累积）。
- `self.object_patches`：整模块替换。
- `self.weight_wrapper_patches`：weight 包装 callable。
- `self.hook_patches` + `current_hooks`/`forced_hooks`：按时间步激活的权重 hook（prompt 加权 LoRA 等）。
- `self.model_options["transformer_options"]`：attn1/attn2/input/output 等切入点的可调用 hook 总线（ControlNet、IPAdapter 在此注入）。

**为什么所有模型修改都挂在 ModelPatcher 而非直接改 nn.Module**：① 同一 backbone 可有多视图（`clone()` 共享 model、分叉 patches）；② 多种 patch 类型需要统一容器；③ 可逆性（每次改动进 backup，`unpatch_model` 精确还原，支持运行时热插拔 LoRA）；④ 设备/内存编排（`load`/`partially_load`/`partially_unload`）。**节点代码不得直接改模型，必须经 ModelPatcher**（`AGENTS.md` 明确要求）。

权重叠加入口：`patch_weight_to_device`（`model_patcher.py:864`）→ `comfy.lora.calculate_weight`（`lora.py:438`）；lowvram 时由 `LowVramPatch.__call__`（`model_patcher.py:154`）在每次 forward 即时计算。

### 4.4 采样流程（`KSampler` → `samplers/sample`）

```
KSampler.sample (节点)                         nodes.py:1606
  └─ common_ksampler                           nodes.py:1555
       ├─ prepare_noise (按 seed 生成噪声)     sample.py:22
       └─ comfy.sample.sample                  sample.py:71
            └─ comfy.samplers.KSampler.sample  samplers.py:1424 (领域对象，按 scheduler 算 sigmas)
                 └─ 模块级 comfy.samplers.sample  samplers.py:1330
                      └─ CFGGuider.sample      samplers.py:1268
                           └─ inner_sample     samplers.py:1214
                                └─ KSAMPLER.sample  samplers.py:982
                                     └─ k_diffusion.sample_euler/dpmpp_2m/...  k_diffusion/sampling.py
```

- **sampler 与 scheduler 解耦**：scheduler 给出 `sigmas` 序列（长度 steps+1，末尾 0），sampler 只消费它（`calculate_sigmas` `samplers.py:1359`）。
- **CFG 由 `CFGGuider` 单类承载**（`samplers.py:1182`，**本版本无 `DualCFGGuider`**）：positive 与 negative 在同一 batch 一次前向（`calc_cond_batch` `samplers.py:220`），公式 `cfg = uncond + (cond - uncond) * scale`（`cfg_function` `samplers.py:591`）。
- **conditioning 结构**：`list[dict]`，每条含 `pooled_output`/`cross_attn`/`model_conds`/`area`/`mask`/`strength`/`control`/`hooks` 等（`comfy/conds.py`）。
- **潜空间 → 图像**：`VAEDecode`（`nodes.py:313`）→ `VAE.decode`（`sd.py:1095`）→ `[N,H,W,C]` IMAGE。

### 4.5 版本化公共 API（`comfy_api/`）

为给第三方客户端提供稳定的节点 IO 契约，ComfyUI 把节点抽象成**版本化 API**：`v0_0_1` / `v0_0_2` / `latest`，由 `comfy_api/internal/api_registry.py` 注册。新节点推荐用 `comfy_api.latest` 的 `io`。`feature_flags.py` 管理"客户端能力探测"（如 `supports_preview_metadata`）。

### 4.6 应用层服务（`app/`）

| 模块 | 职责 |
|---|---|
| `user_manager.py` | 多用户、token、偏好 |
| `model_manager.py` | 模型文件元数据、hash |
| `custom_node_manager.py` | 自定义节点安装/安全检查 |
| `subgraph_manager.py` | 子图（subgraph）封装 |
| `node_replace_manager.py` | 节点迁移/替换 |
| `frontend_management.py` | 前端版本切换（`--front-end-version`） |
| `assets/` | 资产索引（models/input/output 扫描入库） |
| `database/` | SQLAlchemy + Alembic 持久化 |

各 manager 在 `server.py:1210` 的 `add_routes` 里挂载自己的路由。

### 4.7 模型路径管理（`folder_paths.py`）

核心数据结构 `folder_names_and_paths: dict[str, tuple[list[str], set[str]]]`（`:12`）：键是逻辑类型（`checkpoints`/`loras`/`vae`...），值是 `(路径列表, 允许扩展名集合)`。**值里第一项是 list，所以一个类型可挂多个搜索路径**（如 `text_encoders` 同时挂 `models/text_encoders` 和 `models/clip` 旧名）。

- `get_full_path_or_raise`（`:442`）：按 list 顺序在多个路径下找，返回第一个命中——**这是同名文件按路径优先级解析的关键**。
- `add_model_folder_path`（`:354`）：custom_nodes 扩展搜索路径的标准入口；`is_default=True` 插到 list 头（最高优先级）。
- 用户通过 `extra_model_paths.yaml` 注册额外路径（启动时调 `add_model_folder_path`）。

---

## 5. 二次开发入手点

> 原则：优先在**最小归属层**改动（见 `AGENTS.md` "Architecture Boundaries"）；core 严禁加任何联网请求。

| 目标 | 改哪里 | 要点 |
|---|---|---|
| **加自定义节点**（推荐，零侵入） | 新建 `custom_nodes/我的节点/` | 抄 [`custom_nodes/example_tutorial/`](custom_nodes/example_tutorial/)。无需改 core，重启即生效；带前端 UI 就设 `WEB_DIRECTORY` |
| **加内置节点** | `comfy_extras/nodes_xxx.py` + 登记 `nodes.py:2384` | 文件末尾导出 `NODE_CLASS_MAPPINGS`/`NODE_DISPLAY_NAME_MAPPINGS` |
| **加云端 API 节点** | `comfy_api_nodes/nodes_<厂商>.py` | 自动 glob 扫描，参照 `nodes_openai.py`；可 `--disable-api-nodes` 关闭 |
| **支持新模型** | ① `comfy/ldm/<新模型>/` 写网络 ② `comfy/supported_models.py` 注册 ③ `comfy/model_detection.py` 加检测签名 | 检测签名"由特殊到一般"排序，每个取的 key 都要 guard |
| **改采样/调度** | `comfy/samplers.py`、`comfy/sample.py`、`comfy/model_sampling.py` | |
| **改执行/缓存/并发** | `execution.py` + `comfy_execution/` | 不要把 workflow id / 前端 id 泄漏进 execution 层 |
| **加 HTTP/WS 接口** | `server.py`，复杂逻辑抽到 `api_server/routes/` 或 `app/*_manager.py` | |
| **加中间件** | `middleware/`（已有 `cache_middleware.py`） | |
| **加应用服务/持久化** | `app/` 新建 manager + `alembic_db/` 加迁移 | |
| **二开前端 UI** | 去独立仓库 `ComfyUI_frontend` | 本仓库 fortnightly 合一次前端产物 |

**最快练手路径**：复制 `custom_nodes/example_tutorial/` → 改类名/FUNCTION/`NODE_CLASS_MAPPINGS` → 重启 → 在前端双击搜到你的节点。

---

## 6. 启动与运行

### 6.1 环境（Windows + bash，项目根已有 `.venv`）

```bash
cd /d/py-workplace/ComfyUI
source .venv/Scripts/activate

# PyTorch（按显卡二选一）
pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130   # NVIDIA
# pip install torch torchvision torchaudio                                          # CPU only

pip install -r requirements.txt
```

### 6.2 放置模型

```
models/checkpoints/   ← 大模型 .safetensors / .ckpt
models/vae/  models/loras/  models/diffusion_models/  ...
```
复用别的 UI 的目录：复制 `extra_model_paths.yaml.example` → `extra_model_paths.yaml` 编辑。

### 6.3 启动

```bash
python main.py          # 默认 http://127.0.0.1:8188，--auto-launch 自动开浏览器
```

### 6.4 常用参数（`comfy/cli_args.py`）

```bash
python main.py --listen 0.0.0.0 --port 8188     # 局域网访问
python main.py --lowvram                          # 显存不足（智能 offload）
python main.py --cpu                              # 无 GPU 强制 CPU（慢）
python main.py --preview-method auto              # 潜空间预览
python main.py --enable-manager                   # 启用 ComfyUI-Manager
python main.py --disable-api-nodes                # 关闭云端 API 节点（纯离线）
python main.py --front-end-version Comfy-Org/ComfyUI_frontend@latest
```

### 6.5 排错

- `Torch not compiled with CUDA enabled` → `pip uninstall torch` 后用 cu130 命令重装。
- 端口占用 → `--port 8190`。
- OOM → `--lowvram` / `--novram`；动态显存需 PyTorch ≥ 2.8（启动日志有 `DynamicVRAM support detected and enabled`）。
- 节点导入失败 → 看启动日志 `IMPORT FAILED`，通常缺依赖。
- 自定义节点不生效 → 必须重启 `main.py`；放 `custom_nodes/` 下、不带 `.disabled` 后缀。

---

## 7. 开发规范（摘自 `AGENTS.md`）

- **改动最小化**：触及最少文件、最窄代码路径；新增抽象仅在消除真实重复时。
- **层级边界**：每层只关注自己的概念，不把 UI/API/队列/持久化/模型加载泄漏到无关层。`execution.py` 只消费 prompt 图 + 执行状态，产出结果与错误，**不应感知** workflow id、前端 id、持久化 id。
- **禁止联网**：core 不加任何遥测/上报/更新检查/远程配置。
- **接口契约**：修改公共方法时保持调用方约定，不加未使用的兼容参数。
- **模型代码**：不加 `torch.no_grad`/`inference_mode` 包装、不加 freeze/train 开关；模型代码不做内存管理（归 model_management）；优化算子视为不透明。
- **节点**：遵循 `INPUT_TYPES`/`RETURN_TYPES`/`FUNCTION`/`CATEGORY` 约定；节点不得直接 patch 模型，必须经 ModelPatcher。
- **提交**：短动词式 commit message（`Fix ...`/`Add ...`/`Support ...`）。
