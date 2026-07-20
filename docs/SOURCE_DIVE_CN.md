# ComfyUI 三大子系统源码导读（中文）

> 基于 ComfyUI **v0.28.0** 实际源码，所有引用均带 `文件:行号`，经实际打开核对。
> 配合 [`ARCHITECTURE_CN.md`](../ARCHITECTURE_CN.md) 阅读效果更佳。
>
> 本文覆盖最核心的三条链路：
> 1. **执行引擎** —— 从 `POST /prompt` 到逐节点执行、缓存、中断、jobs。
> 2. **模型加载与显存管理** —— 从 checkpoint 文件到可推理 `ModelPatcher`，以及 VRAM offload。
> 3. **采样流程** —— 从 `KSampler` 节点到 k_diffusion 去噪循环、CFG、VAE 解码。

---

## 第一部分：执行引擎（`execution.py` + `comfy_execution/`）

### 1.1 总览

执行引擎负责：接收工作流图 → 校验 → 入队 → 后台线程拓扑溶解逐节点执行 → 增量缓存 → 推进度 → 写历史。涉及两个目录：

- `execution.py`：`PromptExecutor`、`PromptQueue`、`validate_prompt`、`CacheType`。
- `comfy_execution/`：`graph.py`（图 + 拓扑）、`caching.py`（缓存键与策略）、`validation.py`（类型校验）、`jobs.py`（统一作业视图）、`progress.py`。

### 1.2 完整调用链（`POST /prompt` → 结果写回）

```
1. server.py:1063  post_prompt(request)
       ├─ 生成/校验 prompt_id（server.py:1080）
       ├─ server.py:1102  await execution.validate_prompt(...)   ← 同步全图校验
       └─ server.py:1121  self.prompt_queue.put((number, prompt_id, prompt, extra_data, outputs, sensitive))
2. execution.py:1255  PromptQueue.put → heapq.heappush + not_empty.notify()
3. main.py:525  后台线程  threading.Thread(target=prompt_worker, ...)
4. main.py:316  prompt_worker(q, server)   ← 无限循环
       ├─ main.py:346  queue_item = q.get(timeout=...)
       └─ main.py:359  e.execute(item[2], prompt_id, extra_data, item[4])
5. execution.py:724  PromptExecutor.execute → asyncio.run(execute_async)   ← 同步→异步桥
6. execution.py:727  execute_async
       ├─ 构建 DynamicPrompt(graph.py:21)、IsChangedCache、ExecutionList(graph.py:193)
       ├─ execution.py:757  批量查缓存，命中者发 execution_cached
       └─ execution.py:778  while not execution_list.is_empty(): 逐节点 execute
7. execution.py:436  async execute(...)   ← 单节点真正求值
8. main.py:364  q.task_done(item_id, e.history_result, status=..., process_item=remove_sensitive)
9. execution.py:1279  PromptQueue.task_done → self.history[prompt_id] = {...}
10. server.py:1035  GET /history  →  prompt_queue.get_history(...)
```

### 1.3 `PromptExecutor.execute_async`（`execution.py:727`）内部步骤

1. `set_preview_method`；`nodes.interrupt_processing(False)` 清残留中断标志（`:730`）；发 `execution_start`。
2. （仅 `RAM_PRESSURE`）把 `caches.outputs.ram_release` 注册到 `comfy.memory_management`（`:741`）。
3. 进入 `torch.inference_mode()`（`:747`）。
4. 构建动态图 + 缓存预热：`DynamicPrompt`、`IsChangedCache`、对每个 cache `set_prompt` + `clean_unused`（`:748-754`）。
5. **批量查缓存**：`asyncio.gather` 并发查每节点输出，命中进 `cached_nodes`，发 `execution_cached`（`:757-768`）。
6. **拓扑执行循环**（`:778-807`）：
   - `execution_list.stage_node_execution()` 取下一个 ready 节点（`graph.py:236`）。
   - 调 `execute(...)`，按 `ExecutionResult` 分支：
     - `FAILURE` → `handle_execution_error` + `break`。
     - `PENDING` → `unstage_node_execution`（lazy/subgraph/async 等待）。
     - `SUCCESS` → `complete_node_execution`（`graph.py:312`，解锁下游）。
   - `RAM_PRESSURE` 下每步检查 RAM 余量，必要时释放缓存或显存。
7. 收尾：回灌中间输出 UI、发 `execution_success`、组装 `self.history_result`（`:811-830`）。

### 1.4 单节点 `execute`（`execution.py:436`）

```
caches.outputs.get(unique_id) 命中 → 发 executed 消息，返回 SUCCESS      (:444-449)
↓ 未命中
get_input_data (:491)  收集输入（连边值经 execution_list.get_cache 取上游 CacheEntry）
caches.objects.get(unique_id) 命中 → 复用节点实例；否则 class_def() 实例化  (:496-499)
↓
若节点实现 check_lazy_status → 得到 missing 输入名 → make_input_strong_link → 返回 PENDING (:501-518)
↓
get_output_data (:543) → _async_map_node_over_list (:241)  按 list 切片逐次调 obj.FUNCTION
    协程节点未完成 → PENDING (:552-560)
    subgraph 展开（返回 {"expand": graph}）→ 接入新节点 → PENDING (:577-611)
↓
CacheEntry(ui=..., outputs=output_data) → caches.outputs.set            (:613-615)
↓
异常：InterruptProcessingException → FAILURE(无输入格式化) (:617)
      其它异常 → 格式化输入、识别 OOM→unload_all_models → FAILURE(带 traceback) (:626-639)
```

### 1.5 缓存机制

**`CacheType` 四种**（`execution.py:107`，默认在 `main.py:328` 选定）：

| 类型 | 条件 | 实现 |
|---|---|---|
| `RAM_PRESSURE` | **默认** | `RAMPressureCache`（`caching.py:522`），按可用内存压力驱逐 |
| `CLASSIC` | `--cache-classic` | `HierarchicalCache(CacheKeySetInputSignature)` |
| `LRU` | `--cache-lru N` | `LRUCache(max_size=N)`（`caching.py:439`） |
| `NONE` | `--cache-none` | `NullCache`，完全禁用 |

两套缓存：`outputs`（重型张量结果）+ `objects`（节点实例），见 `CacheSet`（`execution.py:114`，`self.all = [outputs, objects]`）。

**缓存键 = 整条祖先链的输入签名**（`CacheKeySetInputSignature` `caching.py:82`）：

- `get_node_signature`（`caching.py:101`）：先 `get_ordered_ancestry`（`:131`）按输入端口排序祖先（保证确定性），再依次拼 `get_immediate_node_signature`（`:109`）：`[class_type, IS_CHANGED 值, (输入名, 值 或 ("ANCESTOR", 祖先索引, socket))]`。
- `IS_CHANGED` / `fingerprint_inputs` 的值由 `IsChangedCache.get`（`execution.py:65`）算；它**故意不喂缓存输出**，只用常量输入（注释 `execution.py:88`），保证 lazy 判定稳定。
- 不可哈希对象（如 tensor）→ `Unhashable()`（`caching.py:50`）；容器递归转 `frozenset`。

**命中 / 失效 / 驱逐**：
- 命中跳过：`execute()` 开头 `caches.outputs.get` 非 None（`execution.py:444`）。
- 失效：`clean_unused`（`caching.py:175`）比较 `get_used_keys()`，不在当前签名的旧 key 被删。上游任一输入或 `IS_CHANGED` 返回值变 → 签名变 → 自动失效。
- LRU 驱逐：按 `generation` 滚动，超 `max_size` 删最老（`caching.py:454`）。
- RAM 压力驱逐：按 `oom_score` 降序逐个释放，直到 `psutil.virtual_memory().available >= target`（`caching.py:583`）；当前代输出不可驱逐。

**存取**：存 `CacheEntry(ui, outputs)`（NamedTuple，`execution.py:102`）；取时 `execution_list.get_cache` 命中还写回主缓存（`graph.py:223` "Write back on touch"）。

### 1.6 校验与执行顺序

**`validate_prompt`（`execution.py:1121`）**：
1. 每节点必须有 `class_type` 且在 `nodes.NODE_CLASS_MAPPINGS`（否则 `missing_node_type`）。
2. 找 `OUTPUT_NODE=True` 的节点（无则 `prompt_no_outputs`）。
3. 对每个输出节点递归 `validate_inputs`（`execution.py:839`）。

**`validate_inputs` 逐项检查**：
- 依赖环检测（`visiting` 栈，`execution.py:847`）。
- 必填缺失（`:894`）、连线长度（`:911`）。
- **类型匹配**：`validate_node_input`（`validation.py:4`）支持 `*` Any、`MatchType`、union（逗号分隔）。
- 标量强转 + min/max（`:985-1038`）、Combo 取值（`:1040`）。
- 自定义校验：V1 `VALIDATE_INPUTS` / V3 `validate_inputs`（`:1075`）。

**执行顺序 = 拓扑溶解**（非一次性排序，而是动态 ready 队列），`TopologicalSort`（`graph.py:106`）：
- `add_node`（`:138`）反向遍历上游登记 strong link；**lazy 连边初始被跳过**（`:159`）。
- `get_ready_nodes`（`:181`）返回 `blockCount==0` 的节点。
- `ExecutionList.stage_node_execution`（`:236`）拿 ready 节点；`ux_friendly_pick_node`（`:269`）在多个 ready 里优先选输出/异步节点，让前端更早看到预览。
- 无 ready 但有 `externalBlocks` → `await unblockedEvent.wait()`；仍无 → `DependencyCycleError`（`:246`）。

**Lazy input**：初始拓扑只跟非 lazy 边；节点 stage 后调 `check_lazy_status`（`execution.py:502`）返回缺失输入名 → `make_input_strong_link`（`graph.py:120`）升级为强依赖 → 返回 `PENDING` → 下次 stage 时上游已就绪。

### 1.7 中断

- 入口：`POST /interrupt`（全局，`server.py:1178`）/ 带 `prompt_id`（`server.py:1158`）/ `/api/jobs/{id}/cancel`（`server.py:961`）。
- 标志位：`interrupt_current_processing`（`model_management.py:2020`）置全局 `interrupt_processing=True`。
- 打断点：每个节点切片前 `before_node_execution`（`execution.py:257` → `nodes.py:48`）就是 `throw_exception_if_processing_interrupted`（`model_management.py:2032`），抛 `InterruptProcessingException`。
- 捕获：`execute()` 的 `except InterruptProcessingException`（`execution.py:617`）→ `handle_execution_error` 发 `execution_interrupted`（broadcast），区别于普通错误的 `execution_error`。
- 每个新 prompt 起跑前清标志（`execution.py:730`）。

### 1.8 Jobs 机制（与老队列共存）

**关键：jobs 不是独立存储**，而是 queue + history 之上的「统一视图 + 归一化」层（`comfy_execution/jobs.py` 全是纯函数）：
- `JobStatus`（`jobs.py:23`）：`pending/in_progress/completed/failed/cancelled`。
- `normalize_queue_item`（`:173`）/ `normalize_history_item`（`:191`）把元组/历史 dict 归一成 job dict。
- `get_all_jobs`（`:368`）/ `get_job`（`:341`）/ `cancel_job`（`:452`）现取 `prompt_queue.get_current_queue_volatile()` + `get_history()` 计算。

`/api/jobs` 路由（`server.py:811` 列表 / `:909` 详情 / `:961` 单个取消 / `:979` 批量取消）与老 `/queue`+`/history` **完全共存**，底层同一份内存数据。历史上限 `MAXIMUM_HISTORY_SIZE = 10000`（`execution.py:1242`）。

---

## 第二部分：模型加载与显存管理（`comfy/sd.py` + `comfy/model_management.py` + `comfy/model_patcher.py`）

> ⚠️ 重要澄清：本仓库**没有 `ModelManager` 类**。真实结构是「模块级函数 + `LoadedModel` 包装类 + 全局 list `current_loaded_models`」。

### 2.1 Checkpoint → ModelPatcher 完整流程

```
CheckpointLoaderSimple.load_checkpoint                          nodes.py:627
  ├─ folder_paths.get_full_path_or_raise("checkpoints", name)    nodes.py:628
  └─ comfy.sd.load_checkpoint_guess_config(ckpt_path, ...)       nodes.py:629 → sd.py:1864
       └─ comfy.utils.load_torch_file(ckpt_path, return_metadata=True)  sd.py:1865 → utils.py:122
       └─ load_state_dict_guess_config(sd, ...)                  sd.py:1931  ← 核心编排器
            ├─ model_detection.unet_prefix_from_state_dict(sd)   sd.py:1938 → model_detection.py:1235
            ├─ comfy.utils.calculate_parameters / weight_dtype   sd.py:1939
            ├─ model_detection.model_config_from_unet(sd, prefix, metadata)  sd.py:1947 → model_detection.py:1219
            │     └─ model_config_from_unet_config               model_detection.py:1211
            │          └─ 遍历 supported_models.models 逐个 matches()   supported_models.py:2345
            ├─ model_config.set_inference_dtype(...)             sd.py:1971
            ├─ model = model_config.get_model(sd, prefix, device=...)     sd.py:1979
            ├─ ModelPatcher = CoreModelPatcher (默认动态)                  sd.py:1980
            ├─ model_patcher = ModelPatcher(model, load_device, offload_device)  sd.py:1982  ← 这就是 MODEL
            ├─ model.load_model_weights(sd, prefix, assign=...)           sd.py:1983
            ├─ vae = VAE(sd=vae_sd, metadata=metadata, device=...)        sd.py:1989
            └─ clip = CLIP(clip_target, state_dict=clip_sd, ...)          sd.py:2018
            → return (model_patcher, clip, vae, clipvision)               sd.py:2031
```

**`load_torch_file`（`comfy/utils.py:122`）**：`.safetensors`/`.sft` 走 `safetensors.safe_open` 逐 key 取（支持 `DISABLE_MMAP`）；其它走 `torch.load(weights_only=True)`。

裸 diffusion model（无 CLIP/VAE）入口 `UNETLoader`（`nodes.py:966`）→ `comfy.sd.load_diffusion_model`（`sd.py:2088`），ModelPatcher 在 `sd.py:2122` 构造。

### 2.2 架构识别（`comfy/model_detection.py` + `comfy/supported_models*.py`）

**`detect_unet_config`（`model_detection.py:44`）** 是识别总入口，按 state-dict key/shape 顺序嗅探：
- `joint_blocks.0.context_block.attn.qkv.weight` → MMDIT（SD3）。
- `double_blocks.0.img_attn.norm...` → Flux / Chroma（再按子键细分 Flux2 / Chroma / Radiance）。
- `txt_in.individual_token_refiner...` → HunyuanVideo；其余分支覆盖 PixArt / LTXV / CogVideoX / Cosmos / Wan 2.1/2.2 / Hunyuan3D / SeedVR / ACE / SAM3 等。

**`model_config_from_unet_config`（`model_detection.py:1211`）**：遍历 `comfy.supported_models.models`（`supported_models.py:2345`，~95 个配置类），第一个 `matches()` 命中的就实例化。**顺序很重要，特殊/细分型号要排在通用之前**（如 `FluxInpaint` 在 `Flux` 前，`WAN22_T2V` 在 `WAN21_T2V` 前）。

**`supported_models_base.BASE`（`supported_models_base.py:31`）** 是基类，类属性：`unet_config` / `latent_format` / `sampling_settings` / `vae_key_prefix` / `text_encoder_key_prefix` / `supported_inference_dtypes` / `memory_usage_factor`。
- `@classmethod matches(s, unet_config, state_dict, prefix)`（`:56`）：对比 `unet_config` 每项 + `required_keys` 是否在 state_dict。
- `model_type(state_dict, prefix)`（`:67`）：默认 `EPS`，子类按 `v_pred`/`edm_*` 等键改写（SDXL 见 `supported_models.py:212`）。
- `get_model(state_dict, prefix, device)`（`:81`）：默认构造 `model_base.BaseModel`，子类可改写（SDXL 构造 `model_base.SDXL`）。

### 2.3 显存管理（`comfy/model_management.py`）

**全局状态**：
- `VRAMState` 枚举（`:43`）：`DISABLED / NO_VRAM / LOW_VRAM / NORMAL_VRAM / HIGH_VRAM / SHARED`，由 CLI 决定（`:548`）：`--lowvram`→LOW_VRAM，`--novram`→NO_VRAM，`--highvram`/`--gpu-only`→HIGH_VRAM，非 GPU→DISABLED，MPS→SHARED。
- `current_loaded_models: list[LoadedModel]`（`:610`）——**唯一**的已加载模型注册表。

**`LoadedModel`（`:694`）**：`weakref` 持有 `ModelPatcher`，方法：
- `model_load(lowvram_model_memory=0, force_patch_weights=False)`（`:735`）：搬上设备 + 应用 patch；`lowvram_model_memory==0` 视为 1e32 = 全量。
- `model_unload(memory_to_free=None, unpatch_weights=True)`（`:758`）：先 `partially_unload`，不够再完全 `detach`。
- `model_use_more_vram(extra_memory)`（`:770`）→ `ModelPatcher.partially_load`。
- `is_dead()`（`:780`）：检测弱引用是否消失（查内存泄漏）。

**主加载器 `load_models_gpu(models, memory_required=0, ...)`（`:860`）**，七步：
1. 推理预算：`inference_memory = minimum_inference_memory()`（0.8GiB + reserved），`extra_mem = max(inference, memory_required + reserved)`。
2. 去重并纳入子 patcher（`model_patches_models`），`reverse` 让首个请求模型最后加载（插表头）。
3. 命中已加载 vs 新增：现有条目置 `currently_used=True` 复用，否则加入待加载。
4. 卸载同源 clone（`is_clone`，`detach(unpatch_all=False)`）。
5. 每设备累积 `total_memory_required`。
6. 两轮 `free_memory`：先按总量*1.1 腾，不够再按 `minimum_memory_required` 腾。
7. **全量 vs 部分加载决策**（`:934-955`，核心）：
   - `lowvram_available and (LOW_VRAM or NORMAL_VRAM) and not force_full_load` → 算 `lowvram_model_memory`（GPU 空闲 - 推理预算，下限 `MIN_WEIGHT_MEMORY_RATIO`，NVIDIA 上为 0），部分加载。
   - `NO_VRAM` → 强制 `0.1`。
   - `force_full_load=True` → `0`（即 1e32 全量）。

**`free_memory(memory_required, device, ...)`（`:816`）**：按 "offload 收益最大、refcount 最小" 排序 `current_loaded_models`，逐个 `model_unload` 直到腾够字节，腾过 `soft_empty_cache()`。

**显存查询**：`get_total_memory`（`:314`）、`get_free_memory`（`:1664`，= device 空闲 + reserved-active）、`minimum_inference_memory`（`:813`）、`maximum_vram_for_weights`（`:1051`）。

**设备选择**：`unet_offload_device`（`:1024`，HIGH_VRAM→GPU 否则 CPU）、`unet_inital_load_device`（`:1030`，注意拼写）、`unet_dtype`（`:1054`，综合 `--fp16-unet/--bf16-unet/--fp8-*` 等）、`text_encoder_device`（`:1137`）、`vae_device/vae_dtype`（`:1195/1206`）。

### 2.4 ModelPatcher（`comfy/model_patcher.py:292`）

包装底层 `nn.Module`（实际是 `model_base.BaseModel`），额外承载的状态字段（`__init__` `:293-352`）：

| 字段 | 用途 |
|---|---|
| `self.patches` | LoRA 等「权重 diff」（按 key 累积 list） |
| `self.backup` | 被 patch 前的原权重（用于 `unpatch` 还原） |
| `self.object_patches` | 整模块替换（按点号路径） |
| `self.weight_wrapper_patches` | 访问时包装某 weight 的 callable |
| `self.model_options["transformer_options"]` | attn1/attn2/input/output 等切入点的 hook 总线 |
| `self.hook_patches` + `current_hooks`/`forced_hooks` | 按时间步激活的权重 hook（prompt 加权 LoRA 等） |
| `self.callbacks` / `self.wrappers` / `self.injections` / `self.attachments` / `self.additional_models` | 跨切关注点挂载点 |

> `CoreModelPatcher`（`:2052`）是 **别名** = `ModelPatcher`；`main.py:265` 运行时重绑为 `ModelPatcherDynamic`（`:1696`，多 GPU 子类）。**不是**独立子类。

**权重 patching 生命周期**：
- `patch_model(device_to, lowvram_model_memory, load_weights, force_patch_weights)`（`:1060`）：应用 object_patches → `load()` 物化 patches → `inject_model()` 装 forward hook。
- `unpatch_model(device_to, unpatch_weights=True)`（`:1077`）：`eject_model` + `unpatch_hooks` + 从 `backup` 还原 + 还原 object_patches。
- `load(...)`（`:929`）：逐 module 决定全量/lowvram-cast/offload；有 patch 的 key 调 `patch_weight_to_device`，lowvram 时装 `LowVramPatch` 到 `m.weight_function`。
- `patch_weight_to_device(key, ...)`（`:846`）：备份 → cast → `comfy.lora.calculate_weight(self.patches[key], temp_weight, key)`（`:864`）→ 写回。

**Patch 注册**（LoRA 等用）：
- `add_patches(patches, strength_patch, strength_model)`（`:789`）：校验 key 存在，追加 `(strength, patches[k], strength_model, offset, function)` 到 `self.patches[key]`，轮转 `patches_uuid`。
- `add_object_patch(name, obj)`（`:684`）、`add_weight_wrapper(name, function)`（`:693`）、`add_hook_patches(...)`（`:1484`）。

**可调用 hook 总线**（全部写入 `transformer_options["patches"]`）：
- 通用：`set_model_patch(patch, name)`（`:616`）、`set_model_patch_replace(...)`（`:622`）。
- 便捷封装：`set_model_attn1_patch`/`attn2_patch`/`attn1_replace`/`attn2_output_patch`/`input_block_patch`/`output_block_patch`/`emb_patch`/`double_block_patch`/`middle_block_after_patch`（`:625-667`）。
- CFG/采样级：`set_model_sampler_cfg_function`（`:593`）、`set_model_sampler_post_cfg_function`（`:601`）、`set_model_unet_function_wrapper`（`:610`）。

> 注意：**没有 `set_model_model_patch`**（仓库内 0 匹配）。`ModelPatcher.calculate_weight`（`:1254`）**已废弃**，内部转调 `comfy.lora.calculate_weight`。

**Hooks 子系统**：`set_hook_mode("MaxSpeed"/"MinVram")`（`:1411`）、`apply_hooks(hooks)`（`:1530`）、`patch_hooks(hooks)`（`:1539`，按 mode 从缓存取或重算权重）、`patch_hook_weight_to_device`（`:1590`，`:1608` 调 `lora.calculate_weight`）。

**Clone**（`:382`）：与原对象**共享** `self.model`（VRAM 平摊），但**各自一份**浅拷贝 `patches`/`object_patches`、深拷贝 `model_options`，设 `n.parent = self`。这是「同一 backbone 多视图」的基础。

### 2.5 LoRA 加载与 patch

```
LoraLoader.load_lora(model, clip, lora_name, strength_model, strength_clip)   nodes.py:719
  ├─ folder_paths.get_full_path_or_raise("loras", lora_name)
  ├─ comfy.utils.load_torch_file(lora_path, safe_load=True, return_metadata=True)
  └─ comfy.sd.load_lora_for_models(model, clip, lora, strength_model, strength_clip, ...)  sd.py:92
       ├─ comfy.lora.model_lora_keys_unet(model.model, key_map)   / model_lora_keys_clip(...)  lora.py:187 / :97
       ├─ lora = comfy.lora_convert.convert_lora(lora)            # 标准化
       ├─ loaded = comfy.lora.load_lora(lora, key_map)            # → {model_key: patch_data}  lora.py:37
       ├─ new_modelpatcher = model.clone()
       ├─ new_modelpatcher.add_patches(loaded, strength_model)    # ← LoRA 进入 ModelPatcher 的瞬间  sd.py:103
       └─ new_clip = clip.clone(); new_clip.patcher.add_patches(loaded, strength_clip)
```

**`load_lora`（`lora.py:37`）**：遍历 `weight_adapter.adapters`（LoRA/LoKR/LoHA/BOFT/OFT/GLora 等适配器类），匹配上则 `adapter_cls.load(...)`；非 adapter 的基本类型是 `("diff", (w,))` 和 `("set", (w,))`。

**`calculate_weight(patches, weight, key, intermediate_dtype, original_weights)`（`lora.py:438`）**：逐项 `(strength, v, strength_model, offset, function)` 处理——`offset` 走 `weight.narrow`，`strength_model≠1` 先乘，`WeightAdapterBase` 交给适配器自算，`("diff",...)` 做 `weight + diff*strength`，`("set",...)` 直接替换。

采样时的权重叠加入口（**不在 k_diffusion 主循环里，全在 patcher 内**）：
- `patch_weight_to_device`（`model_patcher.py:864`）——采样前物化。
- `LowVramPatch.__call__`（`model_patcher.py:154`）——lowvram 模型每个 forward 调 `lora.calculate_weight`。
- `patch_hook_weight_to_device`（`model_patcher.py:1608`）——激活 HookGroup 时。

### 2.6 folder_paths（路径管理）

`folder_names_and_paths: dict[str, tuple[list[str], set[str]]]`（`folder_paths.py:12`）——键是逻辑类型，值是 `(路径列表, 扩展名集合)`。**list 允许多路径**（如 `text_encoders` 同时挂 `models/text_encoders` + `models/clip` 旧名；`diffusion_models` 同时挂 `models/diffusion_models` + `models/unet`）。

- `get_full_path_or_raise`（`:442`）：按 list 顺序在多路径下找，返回首个命中——同名文件按路径优先级解析。
- `add_model_folder_path(name, path, is_default=False)`（`:354`）：custom_nodes 扩展路径的标准入口；`is_default=True` 插表头（最高优先级）。
- `map_legacy`（`:109`）：`{"unet": "diffusion_models", "clip": "text_encoders"}`，旧名兼容。

---

## 第三部分：采样流程（`KSampler` → `comfy/samplers.py` → `k_diffusion`）

> ⚠️ 注意命名陷阱：`comfy/sample.py` 和 `comfy/samplers.py` 各有一个 `sample` 函数；`comfy/samplers.py:1380` 还有一个与节点同名的 `class KSampler`（领域对象）。下文用文件名消歧。

### 3.1 完整调用链

```
KSampler.sample (节点)                                nodes.py:1606
  └─ common_ksampler                                  nodes.py:1555
       ├─ comfy.sample.fix_empty_latent_channels      sample.py:40
       ├─ noise = comfy.sample.prepare_noise(seed)    sample.py:22
       └─ comfy.sample.sample(...)                    sample.py:71    ← 模块级薄封装
            └─ sampler = comfy.samplers.KSampler(...)  samplers.py:1380  (领域对象)
            └─ sampler.sample(noise, pos, neg, ...)    samplers.py:1424
                 └─ 模块级 comfy.samplers.sample(...)  samplers.py:1330
                      ├─ cfg_guider = CFGGuider(model) samplers.py:1182
                      ├─ cfg_guider.set_conds(pos, neg); set_cfg(cfg)
                      └─ cfg_guider.sample(...)        samplers.py:1268
                           └─ outer_sample             samplers.py:1232
                                └─ inner_sample        samplers.py:1214
                                     └─ KSAMPLER.sample  samplers.py:982
                                          └─ sampler_function = sample_euler / sample_dpmpp_2m / ...
                                               (comfy/k_diffusion/sampling.py)
```

### 3.2 `comfy.sample.sample`（`sample.py:71`）—— 注意它本身**不含去噪循环**

只做三件事：构造领域对象 `KSampler`、`calculate_sigmas`、委托给 guider。真正的去噪循环在 k_diffusion。

- **噪声生成**：`prepare_noise(latent, seed, noise_inds)`（`sample.py:22`）用 `torch.manual_seed(seed)` 在 CPU 生成 `randn`；`disable_noise` 时用 `torch.zeros`（`nodes.py:1560`）。
- **sigmas**：`comfy.samplers.KSampler.set_steps`（`samplers.py:1412`）+ `calculate_sigmas`（`:1398` → `:1359`）。

### 3.3 sampler 与 scheduler 的关系（通过 sigmas 解耦）

scheduler 负责「给出长度 steps+1、末尾为 0 的 sigma 序列」；sampler 只消费它。

**`calculate_sigmas(model_sampling, scheduler_name, steps)`（`samplers.py:1359`）**：按 `SCHEDULER_HANDLERS`（`:1346`）分发。
- `use_ms=True`（`simple/normal/beta/ddim_uniform/sgm_uniform/linear_quadratic`）：签名 `handler(model_sampling, steps)`，实现在 `samplers.py:644/670/653/695/710`。
- `use_ms=False`（`karras/exponential/kl_optimal`）：签名 `handler(n, sigma_min, sigma_max)`，实现在 `k_diffusion/sampling.py:23/32/52`。

**sampler 通用签名**（`k_diffusion/sampling.py`，均 `@torch.no_grad()`）：
```python
sample_euler(model, x, sigmas, extra_args=None, callback=None, disable=None,
             s_churn=0., s_tmin=0., s_tmax=float('inf'), s_noise=1.)      # :190
sample_dpmpp_2m(model, x, sigmas, extra_args=None, callback=None, disable=None)  # :796
```
命名规约（`samplers.py:1027`）：`sampler_name="dpmpp_2m"` → `getattr(k_diffusion_sampling, "sample_dpmpp_2m")`。
通用步进（以 `sample_euler` 为例 `:194-211`）：`for i: denoised = model(x, sigmas[i]); d = (x-denoised)/sigma; x += d*(sigmas[i+1]-sigmas[i])`。

### 3.4 CFG 与 negative prompt（`CFGGuider` 单类）

> 本版本**只有 `CFGGuider`（`samplers.py:1182`），没有 `DualCFGGuider`**。三 CFG 等需求通过 `model_options["sampler_cfg_function"]` 注入。

```
CFGGuider(model_patcher)                        samplers.py:1183
  └─ set_conds(positive, negative)              :1189  (经 sampler_helpers.convert_cond 规范化)
  └─ __call__ → outer_predict_noise             :1201/:1204  (WrapperExecutor, 允许自定义节点 wrap)
  └─ predict_noise(x, timestep)                 :1211
       └─ sampling_function(model, x, t, uncond, cond, cfg, ...)  :608
            ├─ if cfg≈1.0: uncond=None（跳过负提示词优化）         :609
            ├─ calc_cond_batch(model, [cond, uncond_], x, t, ...) :619  ← 正负 cond 同 batch 一次前向
            └─ cfg_function(model, out[0], out[1], cfg, x, t, ...) :626 → :591
                 └─ cfg_result = uncond + (cond - uncond) * cfg    :597  ← 标准 CFG 公式
                 └─ 若 sampler_cfg_function 存在 → 改用 x - fn(args)  :592-595  (cfg rescale 等)
```

**批处理**：`_calc_cond_batch`（`:220`）把 positive/negative 放入 `conds=[cond, uncond_]`，`cond_or_uncond` 标记每条属于 `[0,1]`，`model.apply_model`（`model_base.py:192`）**一次前向**跑完，再按 `chunk` 拆开分别累加到 `out_conds[0]/[1]`，最后除以 count（`:336-353`）。这也是 area/mask/strength 的混合点。

**guider 与 model 的关系**：`CFGGuider.inner_model` 在 `outer_sample`（`:1233`）由 `comfy.sampler_helpers.prepare_sampling`（`sampler_helpers.py:202`）赋值为 `BaseModel`（`real_model = model.model`）。k_diffusion 看到的 "model" = `KSamplerX0Inpaint`（包 guider，`:629`）→ `CFGGuider`（CFG 逻辑）→ `BaseModel.apply_model`（真正前向）。

### 3.5 conditioning 数据结构（`comfy/conds.py`）

外层是 `list[dict]`，每条 cond dict 常见键：`pooled_output` / `cross_attn`（CLIP 嵌入）、`model_conds`（`extra_conds` 产出的 COND 包装字典）、`area` / `mask` / `strength` / `start_percent` / `end_percent` / `control`（ControlNet）/ `gligen` / `hooks` / `default`。

`model_conds` 用 COND 包装类（目的：批处理时按 batch_size 复制、按 area 裁剪、判断可 concat、实现 concat）：
- `CONDRegular`（`:26`）：通用包装。
- `CONDCrossAttn`（`:65`）：cross-attn 文本嵌入，`can_concat` 允许序列长度不同时取 `lcm` 填充（最多 4 倍）。
- `CONDNoiseShape`（`:54`）：与 noise 同形的条件（inpaint mask 的 `c_concat`），按 area `narrow` 后 repeat。
- `CONDConstant`（`:98`）：常量条件（ADM 的 `y`），不复制。
- `CONDList`（`:117`）：列表条件。

模型侧由 `model_base.py:322 extra_conds(**kwargs)` 组装，各模型子类 override（60+ 处）。统一预处理在 `samplers.py:1032 process_conds`：`resolve_areas_and_cond_masks_multidim`（`:760`）把百分比 area 转 pixel、`calculate_start_end_timesteps`（`:858`）把 `start_percent/end_percent` 转 sigma 阈值、`encode_model_conds`（`:936`）调模型 `extra_conds`。

### 3.6 ModelPatcher 与采样循环的交互（LoRA / ControlNet / hooks）

LoRA / ControlNet / IPAdapter **不进入 k_diffusion 主循环**，全部由 patcher 在 "model 被调用时" 透明叠加：

- **采样前准备**：
  - `CFGGuider.sample`（`:1268`）→ `prepare_model_patcher`（`:1309` → `sampler_helpers.py:215`）：从 conds 收集 hooks，注册 `WeightHook`/`TransformerOptionsHook`，累加进 `model_options["registered_hooks"]` 与 `transformer_options["patches"]`。
  - `outer_sample`（`:1232`）→ `prepare_sampling`（`sampler_helpers.py:188`）：`get_additional_models`（`:83`）收 ControlNet/GLIGEN，连同主模型 `load_models_gpu`；`self.model_patcher.pre_run()`（`:1251`）触发 `ON_PRE_RUN`。
- **采样中 hook 切换**（每 cond-batch 可能换 hooks）：
  - `_calc_cond_batch`（`:220`）前向之前 `transformer_options = model.current_patcher.apply_hooks(hooks=hooks)`（`:310`）。
  - `apply_hooks`（`model_patcher.py:1530`）→ `patch_hooks`（`:1539`）：按 `hook_mode`（`MaxSpeed`/`MinVram`）从 `cached_hook_patches` 取或重算；备份原权重到 `hook_backup`，`copy_to_param` 写回——**这是 LoRA/hook 推理时改权重的地方**。
  - `prepare_hook_patches_current_keyframe`（`:1414`）：按时间步切 hook 关键帧。
- **ControlNet 注入**（在前向而非权重层）：`_calc_cond_batch`（`:328`）`c['control'] = control.get_control(...)`，随后 `apply_model` → `BaseModel._apply_model` → `diffusion_model(xc, t, ..., control=control, ...)`（`model_base.py:237`）注入到残差块。
- **采样结束清理**：`CFGGuider.sample` 的 `finally`（`:1317`）`restore_hook_patches()`；`outer_sample` 的 `finally`（`:1259`）`model_patcher.cleanup()` + `cleanup_models`。

### 3.7 潜空间 → 图像（VAEDecode）

```
VAEDecode.decode(vae, samples)              nodes.py:330
  └─ vae.decode(latent)                     sd.py:1095
       ├─ memory_used = self.memory_used_decode(...)                    sd.py:1104
       ├─ load_models_gpu([self.patcher], memory_required=...)          sd.py:1105
       ├─ 按 free_memory 算 batch_number 分批                            sd.py:1104-1105
       ├─ self.first_stage_model.decode(samples, ...)                   sd.py:1116-1125
       ├─ OOM → 回退 decode_tiled (1d/2d/3d)                             sd.py:1127-1134
       └─ pixel_samples.movedim(1,-1)   [N,C,H,W] → [N,H,W,C] = IMAGE   sd.py:1156
```

VAE 类（`sd.py:476`）按 state-dict key 嗅探类型（SD1.x / SDXL / Cascade / Wan / HunyuanVideo / Cosmos / LTXV / TAESD 等）。`spacial_compression_decode`（`sd.py:1304`）返回下采样比（SD VAE 为 8）。

**latent shape 约定**：图像 `[N, 4, H/8, W/8]`（SD1.x/SDXL）或 `[N, 16, H/8, W/8]`（SD3/Flux）；视频 5D `[N, C, T, H/8, W/8]`。空 latent 通道由 `fix_empty_latent_channels`（`sample.py:40`）对齐。

---

## 附：三条链路串起来的总览

**执行一次 KSampler 工作流**：
1. 前端 `POST /prompt` → `validate_prompt` → `PromptQueue.put`（`server.py` / `execution.py`）。
2. `prompt_worker` 线程 → `PromptExecutor.execute_async` → 拓扑溶解逐节点执行（`execution.py` / `graph.py`）；缓存命中则跳过。
3. 节点 `CheckpointLoaderSimple` → `load_checkpoint_guess_config` → 架构识别 → 构造 `ModelPatcher`（`sd.py` / `model_detection.py`）。
4. 节点 `LoraLoader` → `ModelPatcher.clone().add_patches`（不改原模型）。
5. 节点 `KSampler` → `common_ksampler` → `comfy.sample.sample` → `CFGGuider` → k_diffusion 去噪循环（`samplers.py` / `k_diffusion`）。
   - 每步：`load_models_gpu` 把权重搬上 GPU（`model_management.py`）→ `apply_hooks` 叠加 LoRA → `apply_model` 前向 → `cfg_function` 合成正负 cond。
6. 节点 `VAEDecode` → `VAE.decode` → `movedim` 成 IMAGE。
7. 节点 `SaveImage` 落盘 → `task_done` 写 history → 前端取结果。

## 关键类 / 方法速查

**执行引擎**
- `PromptExecutor` `execution.py:662` / `execute` `:724` / `execute_async` `:727`
- `execute`（单节点）`execution.py:436`
- `validate_prompt` `execution.py:1121` / `validate_inputs` `:839`
- `PromptQueue` `execution.py:1244` / `put` `:1255` / `get` `:1261` / `task_done` `:1279`
- `DynamicPrompt` `graph.py:21` / `TopologicalSort` `graph.py:106` / `ExecutionList` `graph.py:193`
- `CacheKeySetInputSignature` `caching.py:82` / `HierarchicalCache` `:361` / `LRUCache` `:439` / `RAMPressureCache` `:522`
- `JobStatus` `jobs.py:23` / `get_all_jobs` `:368`

**模型加载 / 显存**
- `load_checkpoint_guess_config` `sd.py:1864` / `load_state_dict_guess_config` `sd.py:1931`
- `detect_unet_config` `model_detection.py:44` / `model_config_from_unet_config` `:1211`
- `supported_models.models` `supported_models.py:2345` / `BASE.matches` `supported_models_base.py:56`
- `ModelPatcher` `model_patcher.py:292` / `patch_model` `:1060` / `add_patches` `:789` / `clone` `:382`
- `load_models_gpu` `model_management.py:860` / `LoadedModel` `:694` / `free_memory` `:816` / `current_loaded_models` `:610`
- `VRAMState` `model_management.py:43`
- `lora.calculate_weight` `lora.py:438` / `load_lora_for_models` `sd.py:92`

**采样**
- `common_ksampler` `nodes.py:1555` / `comfy.sample.sample` `sample.py:71`
- `comfy.samplers.KSampler` `samplers.py:1380` / 模块级 `sample` `:1330`
- `CFGGuider` `samplers.py:1182` / `sampling_function` `:608` / `cfg_function` `:591` / `calc_cond_batch` `:220`
- `KSAMPLER` `samplers.py:976` / `calculate_sigmas` `:1359`
- `VAEDecode` `nodes.py:313` / `VAE.decode` `sd.py:1095`
