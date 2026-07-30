# ComfyUI 学习指南（Java 程序员专版）

> **写给**：会 Java、不会 Python、没系统学过深度学习的工程师。
> **目标**：3–4 周内从「看不懂任何一行」到「能读懂执行引擎、能写自定义节点、能改采样流程」。
> **定位**：本文是「**桥梁**」，不是「架构手册」。架构细节请配合 [`ARCHITECTURE_CN.md`](../ARCHITECTURE_CN.md)（全景）与 [`SOURCE_DIVE_CN.md`](SOURCE_DIVE_CN.md)（三大子系统源码导读）阅读——那两份是给会 Python 的人写的；本文负责把**你缺的三块知识**（Python / 深度学习 / DAG 引擎）补齐，并给出**分阶段、可验收**的学习路线。
> **每个知识点都标注 `文件:行号`**，可直接跳转核对。

---

## 目录

- [0. 先对齐认知：ComfyUI 到底是什么](#0-先对齐认知comfyui-到底是什么)
- [第一篇 知识脉络：补齐你的三个缺口](#第一篇-知识脉络补齐你的三个缺口)
  - [缺口一：Python 速成（Java 程序员视角）](#缺口一python-速成java-程序员视角)
  - [缺口二：深度学习与 AI 推理基础（零基础）](#缺口二深度学习与-ai-推理基础零基础)
  - [缺口三：节点图 / DAG 执行引擎的设计思维](#缺口三节点图--dag-执行引擎的设计思维)
- [第二篇 业务流：一次文生图的完整旅程](#第二篇-业务流一次文生图的完整旅程)
- [第三篇 分阶段学习计划（4 周）](#第三篇-分阶段学习计划4-周)
- [第四篇 二次开发实战](#第四篇-二次开发实战)
- [附录](#附录)

---

## 0. 先对齐认知：ComfyUI 到底是什么

**一句话**：ComfyUI 是一个**用「节点图」来编排 AI 模型推理的服务端引擎**。

把它拆成 Java 程序员能秒懂的三层：

| ComfyUI 层 | 它在干什么 | Java 类比 |
|---|---|---|
| **前端（节点画布）** | 用户拖拽节点、连线，组成一张工作流图 | 一个可视化 BPMN / 流程图编辑器（如 Activiti Designer） |
| **服务端（执行引擎）** | 把这张图当 DAG（有向无环图），**拓扑排序**后逐节点执行，带增量缓存 | 一个自带缓存的 DAG 任务调度器（Airflow / Spring Batch 的 step） |
| **AI 内核（comfy/）** | 每个节点内部真正调 PyTorch 跑模型 | 一堆封装好的算法库调用（像调 OpenCV / DL4J） |

**核心洞察**：执行引擎部分（`server.py` / `execution.py` / `comfy_execution/`）**根本不碰 AI**——它只是个「读图 → 校验 → 拓扑执行 → 缓存」的通用框架。你完全可以用 Java 知识理解它。真正「AI」的部分在 `comfy/` 里，是 PyTorch 模型代码，这部分需要补深度学习基础。

**所以你的学习策略**：先吃掉「执行引擎 + 节点系统」（Java 优势区，快速建立成就感），再去啃 `comfy/` 的 AI 内核。

---

## 第一篇 知识脉络：补齐你的三个缺口

你阅读本项目会卡在三处：**Python 语法/工程机制**、**深度学习概念**、**节点图引擎思维**。逐个补齐。

### 缺口一：Python 速成（Java 程序员视角）

> 不讲 Python 语法大全，只讲**你在 ComfyUI 源码里会高频遇到、且与 Java 差异最大**的 13 个特性。每个都给本项目真实代码定位。

#### 1.1 模块与导入（import）

Python 的一个 `.py` 文件就是一个「模块」（module），一个含 `__init__.py` 的目录就是一个「包」（package）。`import` 既是 Java 的 `import`，也是 Java 的 `ClassLoader`。

- **静态导入**（编译期就知道）：`import folder_paths`（`main.py:16`），等同于 Java 的 `import`。
- **动态导入**（运行时才决定加载谁）——ComfyUI 加载自定义节点的核心：

```python
# nodes.py:2227-2247  load_custom_node 用 importlib 运行时加载任意 .py 文件
async def load_custom_node(module_path, ignore=set(), module_parent="custom_nodes") -> bool:
    module_spec = importlib.util.spec_from_file_location(sys_module_name,
                  os.path.join(module_path, "__init__.py"))
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[sys_module_name] = module          # 注册进全局模块表
    module_spec.loader.exec_module(module)          # 真正执行该文件
```

> **Java 类比**：`Class.forName()` + 自定义 `ClassLoader`。`sys.modules` 就是 Java 的「已加载类表」。
>
> **你会卡住的点**：Python 没有「编译」这一步，`import` 一个文件 = 把它**当场执行一遍**（顶格代码立即运行）。这就是为什么 `custom_nodes/xxx/__init__.py` 里写的注册代码会自动生效——它被 import 时就执行了。

- **目录扫描自动加载**（SPI 风格）：`comfy_api_nodes/` 下所有 `nodes_*.py` 被 glob 扫描自动 import（`nodes.py:2520-2527`），等同于 Java 的 `ServiceLoader`。

#### 1.2 装饰器 `@`

装饰器 = **接收函数、返回函数的高阶函数**，写在被装饰函数上方。ComfyUI 里到处都是。

```python
# server.py:1062-1063  aiohttp 路由注册
@routes.post("/prompt")
async def post_prompt(request):
    ...

# comfy/model_management.py:716  让方法像字段一样被访问
@property
def model(self):
    return self._model()          # _model 是 weakref.ref，必须调用；@property 把这次调用伪装成属性

# nodes.py:57  节点标配：类方法（第一个参数是类 cls 而非实例 self）
@classmethod
def INPUT_TYPES(s):
    return {...}
```

> **Java 类比**：Python 没有直接等价物。最接近的是「注解 + AOP」。
> - `@routes.post("/prompt")` ≈ Spring 的 `@PostMapping("/prompt")`，把下面的函数注册进路由表。
> - `@property` ≈ Lombok 的 getter（把方法调用伪装成字段读取）。
> - `@classmethod` / `@staticmethod` ≈ Java 的 `static` 方法（区别：classmethod 第一个参数自动注入类本身）。
> - `@dataclass` ≈ Lombok `@Data` / Java `record`。
>
> **本质**：`@deco def f(): ...` 等价于 `f = deco(f)`。理解这一点，装饰器就祛魅了。

#### 1.3 async / await + asyncio（异步并发）

这是 ComfyUI 服务端的基石。aiohttp 是**单线程异步**框架。

```python
# server.py:1062  路由处理函数是 async def，内部用 await
@routes.post("/prompt")
async def post_prompt(request):
    json_data = await request.json()       # await = 挂起当前协程，等 IO 完成

# execution.py:557  并发等待多个异步任务
await asyncio.gather(*(self.caches.outputs.get(node_id) for node_id in node_ids))

# execution.py:724-725  从同步入口启动事件循环
def execute(self, prompt, prompt_id, ...):
    asyncio.run(self.execute_async(...))   # 在当前线程里起一个事件循环并阻塞跑完
```

> **Java 类比**：
> - `async def`/`await` ≈ `CompletableFuture` 链 / Reactor 响应式 / **JDK 21 虚拟线程**。本质都是「单线程内的协作式多任务」——遇到 IO 就让出，不真开线程。
> - `asyncio.gather(*tasks)` ≈ `CompletableFuture.allOf(...)`。
> - `asyncio.run()` ≈ 在当前线程 `new Thread(eventLoop).start()` 并阻塞等待结束。
>
> **关键认知**：ComfyUI 是「**主线程跑异步事件循环（HTTP/WS）** + **一个工作线程跑重计算（推理）**」的双线程模型（见 `main.py:525` 启动 `prompt_worker` 守护线程）。这跟 Java 里「Netty IO 线程 + 业务线程池」是同一个套路。

#### 1.4 类型提示（typing）

Python 类型提示**运行时不强制**（除非用 mypy/pyright 静态检查），主要是给人和 IDE 看的。但 ComfyUI 重度使用，你必须看懂。

```python
# comfy/comfy_types/node_typing.py:197  TypedDict：给字典定结构（≈ 紧凑 POJO）
class InputTypeDict(TypedDict):
    required: NotRequired[dict[str, tuple[IO, InputTypeOptions]]]
    optional: NotRequired[dict[str, tuple[IO, InputTypeOptions]]]

# comfy/comfy_types/__init__.py:6  Protocol：鸭子类型接口（不要求显式 implements）
class UnetApplyFunction(Protocol):
    def __call__(self, x: torch.Tensor, t: torch.Tensor, **kwargs) -> torch.Tensor: ...

# comfy/context_windows.py:219  | 联合类型（Python 3.10+ 新语法）
guide_latents: list[torch.Tensor | None]   # = list<Optional<Tensor>>
```

> **Java 类比**：
> - `Optional[T]` / `T | None` ≈ `Optional<T>`。
> - `list[str]` / `dict[str, int]` ≈ `List<String>` / `Map<String, Integer>`。
> - `TypedDict` ≈ 定义一个紧凑的 `record`/POJO。
> - `Protocol` ≈ 接口，但**鸭子类型**（只要对象有这些方法就算「实现」，不用写 `implements`）。
> - `Literal["a", "b"]` ≈ enum，限定取值。
> - `Callable[[A, B], R]` ≈ `Function<A, R>` / `@FunctionalInterface`。
>
> **你会卡住的点**：`dict[str, int]` 和 `Dict[str, int]` 都对（后者来自 `typing` 模块，前者是 3.9+ 内置语法糖）。看到 `**kwargs` 表示「任意个关键字参数」，等同于 Java 的「可变 Map 入参」。

#### 1.5 数据类（dataclass / NamedTuple）

```python
# execution.py:102  NamedTuple：不可变值对象（≈ Java record）
class CacheEntry(NamedTuple):
    ui: dict
    outputs: list

# comfy/context_windows.py:212  @dataclass：可变值对象，带默认值（≈ Lombok @Data）
@dataclass
class WindowingState:
    latents: list[torch.Tensor]
    dim: int = 0                 # 有默认值的字段
    is_multimodal: bool = False
```

> **Java 类比**：`NamedTuple` ≈ `record`（不可变，自动生成构造/getter/equals/hashCode）；`@dataclass` ≈ Lombok `@Data`（可变，自动生成 getter/setter/构造器）。它们省去你写样板代码。

#### 1.6 上下文管理器 `with`

```python
# execution.py:747  把整段推理包进 inference_mode（进入时记录，退出时还原）
with torch.inference_mode():
    dynamic_prompt = DynamicPrompt(prompt)
    ...

# execution.py:1248,1261  配合 threading.Condition 做线程间等待
self.not_empty = threading.Condition(self.mutex)
...
with self.not_empty:                       # = 加锁
    while len(self.queue) == 0:
        self.not_empty.wait(timeout=...)   # = Condition.await()
```

> **Java 类比**：`with` ≈ **try-with-resources**。`with x:` 会在进入时调 `x.__enter__()`，退出时（哪怕异常）调 `x.__exit__()`。
> - `with torch.inference_mode():` ≈ 进入时关掉梯度记录，退出时恢复。
> - `with condition:` ≈ `synchronized` / `lock.lock()`，退出时自动 unlock。
>
> 这是 Java 程序员最舒服的特性——心智模型完全一致。

#### 1.7 生成器与 `yield`

```python
# app/assets/api/routes.py:343  异步生成器：流式分块返回 HTTP body
async def stream_file_chunks():
    with open(abs_path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk: break
            yield chunk                   # 每次 yield 吐一块，调用方按需取
```

> **Java 类比**：**无直接等价**。生成器是「惰性迭代器」，相当于你写了个 `Iterator<T>`，但不用手写 `hasNext()/next()` 状态机——用 `yield` 自动产出。最接近的是 `Stream.generate` 或 reactor 的 `Flux`。
>
> **理解要点**：函数里有 `yield` 就不再是普通函数，调用它**返回一个生成器对象**而不是执行函数体；每 `next()` 一次执行到下一个 `yield` 暂停。

#### 1.8 弱引用 `weakref`

```python
# comfy/model_management.py:703  用 weakref.ref 持有 ModelPatcher（不阻止它被 GC）
def _set_model(self, model: ModelPatcher):
    self._model = weakref.ref(model)
```

> **Java 类比**：`WeakReference<T>`。用途一样：持有对象但「不增加它的可达性」，对象没人用了仍可被回收。ComfyUI 用它来持有模型，避免内存泄漏。配合 `@property def model(self): return self._model()` 每次访问都「解引用」。

#### 1.9 模块级全局状态 / 单例

这是 Python 工程里最让 Java 程序员「不适应」的点：**全局可变状态到处都是，且没有 `private`**。

```python
# comfy/model_management.py:610  模块级 list 单例（已加载模型注册表）
current_loaded_models: list[LoadedModel] = []

# nodes.py:2049  模块级 dict 单例（节点注册表），运行时动态扩展
NODE_CLASS_MAPPINGS = {"KSampler": KSampler, ...}     # 启动时被不断塞入新节点

# server.py:215  把自身实例赋给类属性，做成全局单例
class PromptServer:
    def __init__(self, loop):
        PromptServer.instance = self
```

> **Java 类比**：这是 Python 的「模块级单例」模式，对应 Java 的 `static` 字段 / Spring `@Component` 单例 Bean / 经典 `getInstance()`。
>
> **你会卡住的点**：Python 没有 `private`/`public` 关键字。约定上：名字以 `_` 开头（如 `self._model`）= 「私有，别从外部碰」；以 `__` 开头 = 名字改写（更强隔离）。但都只是**约定**，语言层面不强制。所以 ComfyUI 靠 `AGENTS.md` 这种**文档规范**来约束跨层访问。

#### 1.10 异常体系

```python
# comfy_execution/graph.py:12  自定义异常（继承 Exception）
class DependencyCycleError(Exception): pass

# comfy/model_management.py:2014  特殊中断异常继承 BaseException（避免被通用 except 吞掉）
class InterruptProcessingException(BaseException): pass

# execution.py:617  try/except 多分支
try:
    ...
except InterruptProcessingException as iex:    # = catch
    return (ExecutionResult.FAILURE, ..., iex)
except Exception as ex:                         # 兜底
    typ, _, tb = sys.exc_info()
```

> **Java 类比**：`except` = `catch`，`finally` = `finally`，`raise` = `throw`。
> - **关键差异 1**：Python **没有 checked exception**——不强制声明 `throws`，也不强制捕获。所有异常都是 unchecked。
> - **关键差异 2**：`InterruptProcessingException` 故意继承 `BaseException` 而非 `Exception`，因为 `except Exception` 抓不到 `BaseException` 的子类——这让它像一个「不可被静默吞掉的中断信号」，类似 Java 的 `Error`。
>
> **教训**：ComfyUI 的 `AGENTS.md` 明确反对乱加 `try/except`（见 `AGENTS.md` Python Style 段）。除非是可选依赖探测，否则别用 `try/except` 吞异常。

#### 1.11 推导式（comprehension）

```python
# execution.py:252  字典推导
return {k: v[i if len(v) > i else -1] for k, v in d.items()}

# execution.py:556  带过滤的列表推导
tasks = [x for x in output_data if isinstance(x, asyncio.Task)]
```

> **Java 类比**：直接对应 **Stream API**。
> - `[x for x in xs if p(x)]` ≈ `xs.stream().filter(p).toList()`
> - `{k:v for k,v in m if ...}` ≈ `Collectors.toMap(...)`
>
> Python 推导式比 Stream 更紧凑，是日常首选写法。看多了就习惯了。

#### 1.12 类、继承与魔法方法

```python
# comfy/ops.py:447  多继承（Java 只能单继承类）
class Linear(torch.nn.Linear, CastWeightBiasOp):
    def __init__(self, in_features, out_features, bias=True, ...):
        super().__init__(in_features, out_features, bias, device, dtype)   # = super(...)

# comfy/model_patcher.py:152  __call__：让对象像函数一样可调用
def __call__(self, weight):
    return comfy.lora.calculate_weight(self.patches[self.key], weight, self.key)
# 之后可写 patcher(weight)，像调函数一样调对象
```

> **Java 类比**：
> - `__init__` ≈ 构造器；`super()` ≈ `super(...)`。
> - `__call__` 在 Java 无直接等价（最接近实现 `Function`/`@FunctionalInterface`，让 `obj.apply(x)` 像函数调用）。
> - **最大差异**：Python 支持**多继承**，用 MRO（方法解析顺序，C3 线性化算法）决定方法查找顺序；Java 只能继承一个类 + 实现多个接口。看到 `class A(B, C):` 不要慌，就是同时继承 B 和 C，方法查找按 MRO 顺序。
>
> - 常见魔法方法：`__init__`（构造）、`__call__`（调用）、`__enter__/__exit__`（with）、`__repr__`（toString）、`__len__`、`__getitem__`（`obj[key]`）。它们是 Python 的「运算符重载 + 生命周期钩子」。

#### 1.13 一等公民函数（函数当对象传）

Python 函数是**一等公民**，可以赋值、塞进集合、当参数传递。ComfyUI 节点的核心约定就建立在这一点上：

```python
# nodes.py:1600  节点用【字符串】声明执行入口
class KSampler:
    FUNCTION = "sample"          # ← 只是一个字符串，不是方法引用
    def sample(self, model, seed, ...):
        return common_ksampler(...)

# execution.py:289  执行时用 getattr 把字符串解析成绑定方法再调用
f = getattr(obj, func)           # func = "sample" → 取到 obj.sample 这个方法对象
result = f(**inputs)             # 像普通函数一样调用
```

```python
# comfy/samplers.py:591  把 callable 塞进 dict，运行时取出调用（采样器 hook 总线）
cfg_result = model_options["sampler_cfg_function"](args)   # 直接调用塞进 dict 的函数
for fn in model_options.get("sampler_post_cfg_function", []):
    cfg_result = fn(args)                                  # 函数列表逐个调用
```

> **Java 类比**：
> - `FUNCTION = "sample"` + `getattr` 这种「字符串 → 方法」约定，在 Java 里通常用**反射 `Method.invoke`** 或**策略模式 + Map<String, Strategy>**。ComfyUI 选了反射式，因为节点是用户动态加载的，编译期不知道有哪些方法。
> - 函数塞进 dict ≈ `Map<String, Function<Args, Result>>` + Lambda。
>
> **这是理解 ComfyUI 节点系统的钥匙**：节点不是接口的实现，而是「**一个带若干约定字段的类**」（约定字段：`INPUT_TYPES`/`RETURN_TYPES`/`FUNCTION`/`CATEGORY`），框架用反射读取这些字段来驱动它。详见 [缺口三](#缺口三节点图--dag-执行引擎的设计思维) 和 [节点系统教学最小集](#阶段三第-2-周读懂节点系统并写出第一个自定义节点)。

---

### 缺口二：深度学习与 AI 推理基础（零基础）

> ComfyUI 本质是 AI 推理引擎。你不需要成为 ML 专家，但必须建立「**推理 = 加载权重 → 张量前向 → 拿张量结果**」的最小心智模型。下面 9 个概念 + 一条完整调用链，足够你看懂 `comfy/` 里的核心代码。

#### 2.0 一句话心智模型

> **深度学习推理 = 把训练好的权重（一堆数字）灌进一个 `nn.Module`，把输入数据变成 `Tensor`（多维数组），调用 `forward()` 做一次前向计算，拿到一个 `Tensor` 输出。**
>
> ComfyUI 在此之上做的事 = **显存搬运**（让大模型在小显存上也能跑）+ **模块化拼装**（用节点把多个模型拼起来）。

#### 2.1 `torch.Tensor`（张量 = 多维数组）

Tensor 就是住在 CPU 或 GPU 内存里的多维数组，是 PyTorch 的基本数据单元。

**ComfyUI 约定**：`IMAGE` 类型 = 一个 shape 为 `[N, H, W, C]`、值域 `[0,1]` 的 float 张量（`N`=batch/帧数、`H`=高、`W`=宽、`C`=通道数，**通道在最后一维**）。

```python
# nodes.py:1681  SaveImage 把 IMAGE tensor 转回 PNG，反推可知 IMAGE 的结构
i = 255. * image.cpu().numpy()                 # tensor → CPU → numpy；乘 255 说明原值域 [0,1]
img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))  # fromarray 接受 [H,W,C]
```

> **Java 类比**：`Tensor` ≈ `double[][][][]` 多维数组，但两个关键不同：
> ① 它可以住在 GPU 显存（`tensor.to("cuda")` 才能用 GPU 算）；
> ② 自带 batch 维（`N`）。
> 如果你用过 ND4J/DL4J 的 `INDArray`，那就是几乎一对一的概念。可把 IMAGE 想成 `record IMAGE(INDArray data /* [N,H,W,C], float in [0,1] */) {}`。

**坑**：PyTorch 卷积惯例是 `[N, C, H, W]`（通道在第二维），所以卷积前要换轴：`image.movedim(-1, 1)`（`nodes.py:903`），把 `[N,H,W,C]` → `[N,C,H,W]`。

#### 2.2 `nn.Module` + `forward()`（模型 = 抽象基类 + 模板方法）

PyTorch 所有模型都继承 `torch.nn.Module`，在 `__init__` 声明子模块，在 `def forward(self, ...)` 定义前向计算。

```python
# comfy/model_base.py:153  ComfyUI 顶层模型类 BaseModel
class BaseModel(torch.nn.Module):
    def __init__(self, model_config, ...):
        super().__init__()
        ...
        self.diffusion_model = unet_model(**unet_config, ...)   # 声明子模块（UNet）
        self.diffusion_model.eval()

# comfy/ldm/modules/diffusionmodules/openaimodel.py:837  真正的 forward 在 UNetModel
class UNetModel(nn.Module):
    def _forward(self, x, timesteps=None, context=None, y=None, control=None, ...):
        """Apply the model to an input batch.
        :param x: an [N x C x ...] Tensor of inputs.
        :param context: conditioning plugged in via crossattn"""
```

> **语法糖**：`model(x, t, ...)` 会被 PyTorch 自动翻译成 `model.forward(x, t, ...)`（通过 `__call__`）。所以采样器里看到 `model(x, sigma, **extra_args)`，那就是在调 UNet 的 forward。
>
> **Java 类比**：
> ```java
> abstract class Module {                       // ≈ torch.nn.Module
>     List<Module> children;                    // 子模块树（__init__ 里 self.xxx = ... 自动登记）
>     abstract Tensor forward(Tensor x, ...);   // 模板方法，子类实现
> }
> class UNetModel extends Module {
>     private Module timeEmbed, inputBlocks, middleBlock, outputBlocks;
>     @Override Tensor forward(Tensor x, Tensor t, Tensor context, ...) { ... }
> }
> ```
> `nn.Module` ≈「抽象基类 + 模板方法 `forward`」+ 一个由容器自动管理的子模块树。

#### 2.3 `state_dict` / 权重加载（checkpoint 就是个 `Map<String, Tensor>`）

一个 `.safetensors`/`.ckpt` 文件本质上是个 `dict[str, Tensor]`：key 是参数名（如 `input_blocks.1.1.transformer_blocks.0.attn1.to_q.weight`），value 是该参数的张量。加载 = 把这个字典「按 key 名」灌进 `nn.Module` 的字段树。

```python
# comfy/sd.py:1864  加载 checkpoint 的入口
def load_checkpoint_guess_config(ckpt_path, ...):
    sd, metadata = comfy.utils.load_torch_file(ckpt_path, return_metadata=True)  # 文件 → dict
    out = load_state_dict_guess_config(sd, ...)                                 # dict → 模型对象

# comfy/model_base.py:346  把权重灌进 nn.Module（调 PyTorch 原生 load_state_dict）
def load_model_weights(self, sd, unet_prefix="", assign=False):
    to_load = {k[len(unet_prefix):]: sd.pop(k) for k in list(sd) if k.startswith(unet_prefix)}
    m, u = self.diffusion_model.load_state_dict(to_load, strict=False, assign=assign)
```

> **`strict=False`**：多余的 key 忽略、缺失的 key 不报错。这是 ComfyUI 能兼容 diffusers / 原版 / 各种魔改权重的关键。
>
> **Java 类比**：相当于反序列化装配对象，但更「裸」——checkpoint 是 `Map<String, float[]>`，`load_state_dict` 按 key 路径（`diffusion_model.time_embed.0.weight`）找到字段塞进去。可类比 Jackson 的 `@JsonProperty` 隐式映射，只不过这里用「字段名路径」。比 Java 序列化更弱类型。

#### 2.4 `device`（设备）与 `dtype`（精度）—— ComfyUI 最核心的工程难点

- **device**：tensor/模块当前住在哪块硬件（CPU/GPU）。`.to(device)` 是搬运操作。

```python
# comfy/model_management.py:193  决定当前用哪个设备
def get_torch_device():
    if cpu_state == CPUState.CPU: return torch.device("cpu")
    ...
    return torch.device(torch.cuda.current_device())   # NVIDIA/AMD 走这条
```

- **dtype**：精度。`float32`（4 字节，最准最费显存）/ `float16`·`bfloat16`（2 字节，GPU 推理默认）/ `float8_e4m3fn`（1 字节，fp8 量化）。

```python
# comfy/model_management.py:1464  搬运 + 精度转换一站式工具（ComfyUI 到处调）
def cast_to(weight, dtype=None, device=None, ...): ...
# comfy/model_management.py:1492
def cast_to_device(tensor, device, dtype, copy=False): ...
```

> **Java 类比**：
> - `device` ≈「数据在堆内（CPU）还是堆外 GPU 缓冲区」。类似 `ByteBuffer.allocate()` vs `allocateDirect()`，但 PyTorch 的 `.to("cuda")` 会做真正内存拷贝。
> - `dtype` ≈ `double`（fp32）vs `short`（fp16）vs `byte`（fp8）。精度越低越省内存越不准。
> - **为什么是工程难点**：模型动辄 4–12GB，GPU 显存可能只有 8GB。ComfyUI 的 `load_models_gpu`（`model_management.py:860`）要在「模型权重 / 输入张量 / 中间激活」三者间动态腾挪——lowvram 模式下甚至每个子模块用完就卸回 CPU。这就像在 Java 里写一个 LRU 内存池，在堆和堆外之间不停搬运 `byte[]`。
>
> **`AGENTS.md` 的死命令**（`AGENTS.md` Model/Device 段）：模型代码自己**不做**内存管理、不乱加 dtype 转换、不 `cpu()` 后又搬回去。这些归 `comfy.model_management` 统一管。

#### 2.5 `inference_mode` / `no_grad`（推理时不记录梯度）

整个图的执行被包在 `with torch.inference_mode():` 里。它告诉 PyTorch：接下来的所有 forward **都不构建自动求导图**，从而省内存、加速。这是「推理」与「训练」的本质区别。

```python
# execution.py:747  最外层全局包好
with torch.inference_mode():
    dynamic_prompt = DynamicPrompt(prompt)
    ...
```

> **关键规则**（`AGENTS.md:118-122`）：ComfyUI 已在最外层包好 inference_mode，**任何节点/模型代码都禁止自己再加 `torch.no_grad()` / `torch.inference_mode()`**。写自定义节点时别画蛇添足。
>
> **Java 类比**：自动求导 ≈ 一个隐式 AOP，记录每次张量运算以便后续反向算梯度（训练才需要）。`inference_mode()` ≈ 关掉这层 AOP（类似 `@Transactional(propagation=NEVER)`，进入上下文就不再记录）。推理只调 `forward()`，记录求导历史纯属浪费。

#### 2.6 `ModelPatcher`（ComfyUI 最关键抽象 = 模型的可变装饰层）

这是 ComfyUI 区别于其他推理框架的灵魂。`ModelPatcher` 包装底层 `nn.Module`，额外承载「可叠加、可回滚」的修改层。

```python
# comfy/model_patcher.py:292  关键属性（精简）
class ModelPatcher:
    def __init__(self, model, load_device, offload_device, ...):
        self.model = model                          # 被包装的 nn.Module
        self.patches = {}                           # LoRA 等「权重 diff」，按 key 累积
        self.object_patches = {}                    # 整模块替换
        self.weight_wrapper_patches = {}            # weight 包装 callable
        self.model_options = {"transformer_options": {}}  # ControlNet/IPAdapter 注入总线
        self.hook_patches = {}                      # 按时间步激活的权重 hook（prompt 加权 LoRA）
        self.load_device = load_device
        self.offload_device = offload_device
```

> **为什么所有模型修改都挂在 ModelPatcher 而非直接改 nn.Module**（`ARCHITECTURE_CN.md:191`）：
> ① 同一 backbone 可有多视图（`clone()` 共享 model、分叉 patches）；
> ② 多种 patch 类型需要统一容器；
> ③ **可逆性**（`unpatch_model` 精确还原，支持运行时热插拔 LoRA）；
> ④ 设备/内存编排（`load`/`partially_load`/`partially_unload`）。
>
> **`AGENTS.md` 死命令**：节点代码**不得直接改模型**，必须经 ModelPatcher。
>
> **Java 类比**：ModelPatcher ≈ **动态代理 / 装饰器模式 / Spring AOP 的 `MethodInterceptor` 链**。
> ```java
> class ModelPatcher implements InvocationHandler {
>     private Module realModel;
>     private Map<String, WeightDiff> patches;       // LoRA 增量
>     private Map<String, Module> objectPatches;     // 整模块替换
>     public Tensor forward(Tensor x, ...) {
>         applyPatches();           // 把增量叠加到权重
>         realModel.forward(...);   // 真正调用
>         unpatch();                // 还原，支持下次叠加不同的 LoRA
>     }
> }
> ```
> 它的价值是「**不改原始对象的前提下，给方法加可叠加、可回滚的层**」——这正是 LoRA、ControlNet、IPAdapter 能像乐高一样拼装的原因。

#### 2.7 采样 / 去噪循环（扩散模型的核心数学）

扩散模型生成 ≈ **一个 20–30 轮的 while 循环，每轮调一次 `unet.forward()`**。

```python
# comfy/k_diffusion/sampling.py:190  Euler 采样器（最简单）
def sample_euler(model, x, sigmas, extra_args=None, ...):
    s_in = x.new_ones([x.shape[0]])
    for i in trange(len(sigmas) - 1, disable=disable):
        sigma_hat = sigmas[i]
        denoised = model(x, sigma_hat * s_in, **extra_args)   # ① 调一次 forward：预测去噪后样子
        d = to_d(x, sigma_hat, denoised)                      # ② 算「去噪方向」
        dt = sigmas[i + 1] - sigma_hat
        x = x + d * dt                                        # ③ 沿方向走一小步（欧拉法）
    return x
```

> **Java 类比**：
> ```java
> Tensor x = randomNoise(latentShape);               // 初始纯噪声
> for (int i = 0; i < steps; i++) {
>     Tensor denoised = unet.forward(x, sigmas[i], conditioning);  // AI 预测
>     Tensor d = toD(x, sigmas[i], denoised);
>     x = x.add(d.mul(dt));                           // 走一步
> }
> return x;   // 干净的 latent
> ```
> Steps 越多越精细越慢。更复杂的 `sample_dpmpp_2m`（`sampling.py:796`）数学更花哨，但骨架一样。

#### 2.8 VAE encode / decode（潜空间 ↔ 像素图）

**为什么生图分两步**：扩散过程**不**在像素空间跑（512×512×3 太大太慢），而是在压缩后的 latent 空间（64×64×4）跑完去噪循环，最后用 VAE 把 latent 解码回像素图。

```python
# comfy/sd.py:1095  VAE.decode：latent → 像素图
def decode(self, samples_in, vae_options={}):
    ...
    samples = samples_in[x:x+batch_number].to(device=self.device, dtype=self.vae_dtype)
    out = self.first_stage_model.decode(samples, ...)

# comfy/sd.py:1191  VAE.encode：像素图 → latent
def encode(self, pixel_samples):
    pixel_samples = pixel_samples.movedim(-1, 1)    # [N,H,W,C] → [N,C,H,W]
    out = self.first_stage_model.encode(pixels_in)
```

> VAE 自身就是一个小编码器-解码器网络（`downscale_ratio = 8`，即 512×512 像素 ↔ 64×64 latent，`sd.py:489`）。
>
> **Java 类比**：VAE ≈ JPEG 的有损压缩/解压，但压缩/解压都是学出来的神经网络。生图流程 ≈ `randomNoise → [压缩域去噪 N 步] → VAE.decode → 像素图`。

#### 2.9 CLIP / 文本编码（文本 → conditioning）

文本编码器也是个 `nn.Module`，把 prompt 字符串变成一组语义向量（conditioning），喂给 UNet 的 cross-attention 指导画什么。

```python
# nodes.py:73  CLIPTextEncode 节点
def encode(self, clip, text):
    tokens = clip.tokenize(text)                              # 文本 → token id 列表
    return (clip.encode_from_tokens_scheduled(tokens), )      # token → conditioning
```

**conditioning 结构**：`list[tuple[Tensor, dict]]`，每项 = `[cond_tensor, {"pooled_output":..., "area":..., "mask":..., "strength":..., "control":..., "hooks":...}]`（`ARCHITECTURE_CN.md:212`）。`cond_tensor` 会被注入 UNet forward 的 `context` 参数（见 2.2）。

> **Java 类比**：CLIP ≈ 一个把字符串映射成稠密向量的预训练 Embedding 服务。
> ```java
> record Conditioning(List<CondEntry> entries) {}
> record CondEntry(Tensor cond, Map<String,Object> meta) {}
> ```

#### 2.10 把它串起来：文生图全程调用链

这一条链是整个 AI 内核的脊梁，务必背下来：

```
1. CheckpointLoaderSimple ── nodes.py:627 → comfy/sd.py:1864
     文件 → Dict[str,Tensor](state_dict) → nn.Module → ModelPatcher 包装
     同时产出 MODEL / VAE / CLIP

2. CLIPTextEncode("a cat") ── nodes.py:73 → sd.py:325 → clip_model.py:163
     文本 → conditioning: list[tuple[Tensor,dict]]

3. EmptyLatentImage ── 随机噪声 Tensor [N,4,H/8,W/8]

4. KSampler ── 进入 with torch.inference_mode():(execution.py:747)
     ↓ k_diffusion/sampling.py:190 sample_euler
     for i in steps:
       denoised = model(x, sigma, conditioning)   # UNet.forward
       x = x + d * dt                             # 走一步

5. VAEDecode ── nodes.py:330 → sd.py:1095
     latent → VAE.decode → IMAGE [N,H,W,C]

6. SaveImage ── nodes.py:1681
     255 * image.cpu().numpy() → PNG 落盘
```

> **device/dtype（2.4 节）贯穿每一步**：每张张量、每个模型都要决定住 CPU 还是 GPU、用 fp32 还是 fp16。`cast_to_device` 和 `load_models_gpu` 在背后不停搬运——这就是 ComfyUI 作为「AI 推理引擎」的核心工程价值。

---

### 缺口三：节点图 / DAG 执行引擎的设计思维

> 这是 ComfyUI 的**架构灵魂**。好消息：这一层**完全不碰 AI**，是纯软件工程，你的 Java 功力直接可用。

#### 3.1 节点图 = DAG（有向无环图）

用户在画布上连出来的工作流，就是一个 DAG：节点是顶点，连线是有向边，数据沿边流动，**不能成环**。ComfyUI 的工作流 JSON 就是这个图的序列化：

```json
{ "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["4", 0]}}, ... }
//                                              ↑ 文本参数          ↑ "clip" 来自节点 4 的第 0 个输出
```

> **Java 类比**：DAG 任务调度。Airflow 的 DAG、Spring Batch 的 Step、Spark 的 RDD 血缘、响应式流的算子图——都是同一个东西。`["4", 0]` 这种「`[上游节点id, 输出端口序号]`」引用，就是图的邻接关系。

#### 3.2 从「输出节点」反向决定要算什么（拉模型）

ComfyUI **不是**从头到尾顺序执行整张图，而是**从标记为 `OUTPUT_NODE = True` 的「终点节点」（如 SaveImage）反向遍历**，决定哪些节点需要执行。

```python
# nodes.py:1643  SaveImage 声明自己是终点
class SaveImage:
    OUTPUT_NODE = True          # ← 关键：没有这个标志的节点，若无下游连到终点，就永远不执行（死节点）
```

> **Java 类比**：`OUTPUT_NODE=True` ≈ 响应式图的 sink / `@RequestMapping` 入口。其它节点是 lazy 的中间算子，只有被 sink 拉取时才求值。这就是为什么你拖一堆节点但没连到 SaveImage，点 Queue 也不会执行它们。
>
> 这也解释了 `validate_prompt`（`execution.py:1121`）的第一步：找所有 `OUTPUT_NODE=True` 的节点，没有就报 `prompt_no_outputs`。

#### 3.3 拓扑执行 + 增量缓存（ComfyUI 的性能灵魂）

执行引擎对每个节点算一个**缓存 key** = `[节点类型, IS_CHANGED 指纹, 上游输入签名...]`（`comfy_execution/caching.py:116`）。命中就直接复用上次结果，不重算。

```python
# custom_nodes/example_tutorial/image_nodes.py:90  节点自定义缓存指纹
@classmethod
def IS_CHANGED(cls, image, brightness, contrast, saturation, invert=False):
    return (brightness, contrast, saturation, invert)   # 返回指纹值；变了就重算

# nodes.py:1783  LoadImage 用文件 sha256 当指纹（文件内容变了才重算）
@classmethod
def IS_CHANGED(s, image):
    ...
    return m.digest().hex()
```

> **`IS_CHANGED` 的本质**：让节点声明「除了输入连接，还有哪些外部状态（文件、时间、随机种子）会影响输出」，用来补全缓存 key。
> - 不实现 → 默认：任一输入变才重算。
> - 返回 `float("nan")` → 每次**强制重算**（因为 `nan != nan` 恒成立），用于随机种子节点。
>
> **Java 类比**：Spring `@Cacheable(key="...")` 里的 SpEL 表达式；`IS_CHANGED` 就是让你自定义 key 的一部分。这就是 README 说的「**只重算变化的部分**」的底层原理。
>
> **四种缓存策略**（`execution.py:107`，默认在 `main.py:328` 选定）：
> | 类型 | 触发 | 实现 |
> |---|---|---|
> | `RAM_PRESSURE`（默认） | 按可用内存压力驱逐 | `RAMPressureCache` |
> | `CLASSIC` | `--cache-classic` | `HierarchicalCache` |
> | `LRU` | `--cache-lru N` | `LRUCache(max_size=N)` |
> | `NONE` | `--cache-none` | 完全禁用 |

#### 3.4 类型系统 = 字符串约定 + 集合运算（没有继承/子类型）

ComfyUI 的「类型」是**纯字符串**，类型校验是**字符串集合运算**，不是面向对象的类型系统。

```python
# comfy_execution/validation.py:4  一根连线合法吗？纯字符串比较
def validate_node_input(received_type: str, input_type: str, strict: bool = False) -> bool:
    if received_type == input_type: return True                       # 完全相等
    if received_type == "*" or input_type == "*": return True         # "*" = Any 通配
    received_types = set(received_type.split(","))                    # 逗号 = 联合类型
    input_types = set(input_type.split(","))
    return len(received_types & input_types) > 0                      # 有交集即可
```

> **要点**：`"IMAGE"`/`"MODEL"`/`"CLIP"` 是字符串约定（底层用 `@comfytype(io_type="IMAGE")` 把字符串绑到 `io.Image` 类，`comfy_api/latest/_io.py:420`）。`"*"` 通配，`"STRING,INT"` 联合。**没有继承/子类型**，靠精确匹配或显式联合。
>
> **Java 类比**：像 Spring 的 `@Qualifier` + 字符串匹配，而不是 Java 的 `instanceof` 多态。这种设计让类型校验极简、可序列化、跨语言（前端 TS 也能用同一套字符串约定）。

#### 3.5 并发模型（生产者-消费者 + 单线程事件循环）

| 角色 | 位置 | Java 类比 |
|---|---|---|
| 主线程：aiohttp 事件循环（HTTP/WebSocket） | `main.py:573` `event_loop.run_until_complete(...)` | Netty EventLoop / Vert.x / 虚拟线程 |
| `prompt_queue`：堆队列 + 条件变量 | `execution.py:1244`，`heapq` + `threading.Condition` | `PriorityBlockingQueue` |
| `prompt_worker`：守护线程消费队列 | `main.py:316` `while True: q.get()` | 消费者线程 / `ExecutorService` worker |

```python
# main.py:316  消费者线程（Java 程序员最熟悉的 BlockingQueue 消费模式）
def prompt_worker(q, server_instance):
    while True:
        queue_item = q.get(timeout=timeout)        # 阻塞等任务
        if queue_item is not None:
            ...
            e.execute(item[2], prompt_id, extra_data, item[4])   # 跑推理（重计算放这里，不阻塞 IO）
            q.task_done(item_id, e.history_result, ...)          # 写回 history
```

> **为什么这样设计**：HTTP/WS 不能被慢推理阻塞（否则前端连进度都收不到）。所以 IO 在事件循环线程，推理在 worker 线程；推理进度通过 `hijack_progress`（`main.py:422`）钩子劫持后，用 `send_sync` 推 WS 给前端。这是教科书式的「IO 线程与计算线程分离」。

#### 3.6 中断机制（协作式中断）

```python
# comfy/model_management.py:2014  中断异常继承 BaseException，避免被 except Exception 吞
class InterruptProcessingException(BaseException): pass
```

`POST /interrupt` → 设全局标志位 → 在采样循环的每个切片前 `throw_exception_if_processing_interrupted` 检查并抛出。这是**协作式中断**（在安全点检查），不是 Java 的 `Thread.interrupt()` 抢占式。

---

## 第二篇 业务流：一次文生图的完整旅程

把前面的知识点串成一条端到端的业务流。这是你理解整个项目的「主干道」。

```
用户在前端画布连好图，按 Ctrl+Enter（Queue Prompt）
  │
  ▼ POST /prompt  （工作流 JSON）             server.py:1063
┌─────────────────────────────────────────────────────────┐
│ PromptServer (aiohttp, 主线程事件循环)                   │
│   ① 生成 prompt_id                                       │
│   ② execution.validate_prompt  全图同步校验               │ ← execution.py:1121
│      （节点存在性 / 连线合法 / 类型匹配 / 环检测 / 必填）  │
│   ③ prompt_queue.put((number, prompt_id, prompt,         │ ← execution.py:1255
│        extra_data, outputs_to_execute, sensitive))       │
└─────────────────────────────────────────────────────────┘
  │  notify() 唤醒
  ▼
┌─────────────────────────────────────────────────────────┐
│ prompt_worker 守护线程 (main.py:316, while True)         │
│   ④ q.get() 取出任务                                     │
│   ⑤ PromptExecutor.execute → asyncio.run(execute_async)  │ ← execution.py:724
└─────────────────────────────────────────────────────────┘
  │
  ▼ execute_async (execution.py:727)  ← with torch.inference_mode():
┌─────────────────────────────────────────────────────────┐
│  ⑥ 构建 DynamicPrompt / IsChangedCache / ExecutionList   │
│  ⑦ 批量查缓存：asyncio.gather 查每节点，命中的发          │
│     execution_cached 事件给前端，跳过执行                 │ ← execution.py:757
│  ⑧ 拓扑执行循环 (ExecutionList 拓扑溶解)：                │ ← graph.py:193
│       while not empty:                                   │
│         stage_node_execution() 取下一个 ready 节点         │
│         execute(单节点) → 调 obj.FUNCTION → 写 CacheEntry │ ← execution.py:436
│         complete_node_execution() 解锁下游                 │
│  ⑨ 推进度：hijack_progress 钩子 → WS 推 'progress'        │ ← main.py:422
└─────────────────────────────────────────────────────────┘
  │  每个节点的 FUNCTION 内部
  ▼  （这里才是 AI，见「缺口二 §2.10」全程调用链）
┌─────────────────────────────────────────────────────────┐
│  CheckpointLoader → ModelPatcher                          │
│  CLIPTextEncode  → conditioning                           │
│  KSampler        → k_diffusion 去噪循环（调 UNet.forward） │
│  VAEDecode       → IMAGE                                  │
│  SaveImage       → PNG 落盘 output/                       │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  ⑩ q.task_done → history[prompt_id] = {outputs, status}   │ ← execution.py:1279
│  ⑪ 前端轮询 GET /history 或新版 /api/jobs 取回结果         │ ← server.py:1035 / :811 │
└─────────────────────────────────────────────────────────┘
```

**队列元组结构**（`server.py:1121`，消费于 `main.py:350`）：

| 位置 | 字段 | 含义 |
|---|---|---|
| `[0]` | `number` | 优先级（堆用；`front=True` 取负数插队） |
| `[1]` | `prompt_id` | UUID |
| `[2]` | `prompt` | 完整图字典 `{node_id: {inputs, class_type}}` |
| `[3]` | `extra_data` | client_id / extra_pnginfo |
| `[4]` | `outputs_to_execute` | 要执行的终点节点 id 列表 |
| `[5]` | `sensitive` | 敏感字段（如 token），**不入 history** |

> 详细到每一行的源码导读，见 [`SOURCE_DIVE_CN.md`](SOURCE_DIVE_CN.md)。本节的目的是让你建立**端到端直觉**，而不是记住每一行。

---

## 第三篇 分阶段学习计划（4 周）

> 原则：**先 Java 优势区（引擎/节点），后 AI 内核**；每个阶段都有明确的「读什么 / 做什么 / 怎么算完成」。每阶段配**验收标准**，做不到就别进下一阶段。

### 阶段 0：环境准备（半天）

**目标**：本地跑起来，亲眼看到出图。

**做**：
1. 激活 venv：`source .venv/Scripts/activate`（项目根已有 `.venv`）。
2. 装依赖：`pip install -r requirements.txt`（PyTorch 参考 `README.md` NVIDIA 段）。
3. 放模型：`models/checkpoints/` 放一个 `.safetensors`（如 SD1.5 / SDXL）。
4. 启动：`python main.py`，浏览器开 `http://127.0.0.1:8188`。
5. 用默认图或拖入根目录的 `sd15_txt2img.json`，点 Queue，等出图。

> ⚠️ 模型下载：huggingface.co 在你的网络不可达，用 [hf-mirror.com](https://hf-mirror.com) 或 modelscope.cn 下载。

**验收**：能生成一张图。能看懂启动日志里 `IMPORT FAILED` / `Import times for custom nodes` 等信息。

---

### 阶段 1：Python 速成（第 1 周，前 3 天）

**目标**：能无障碍阅读 `main.py` / `server.py` / `execution.py` 的 Python 代码。

**读**：本文 [缺口一](#缺口一python-速成java-程序员视角) 的 13 个特性，**逐个去源码里找到对应行号**实地看一遍。

**动手**（在 Python REPL 里，不用碰项目）：
- 写一个带 `@dataclass` + `@property` + `__call__` 的小类，理解装饰器和魔法方法。
- 写一个 `async def` + `asyncio.run` 的例子，理解协程。
- 写一个生成器 `def gen(): yield ...`，用 `next()` 取值，理解 `yield`。
- 写一个继承两父类的类，`print(Child.__mro__)` 看 MRO。

**验收**：能口头解释下面这段 `execution.py:289` 代码每一行的含义：
```python
f = getattr(obj, func)
result = f(**inputs)
```
（答案：`getattr` 把字符串方法名解析成绑定方法，`**inputs` 把 dict 解包成关键字参数调用。）

**推荐补充资料**：Python 官方 Tutorial（docs.python.org/zh-cn/3/tutorial/），只需读「类」「错误和异常」「标准库概览」几节。

---

### 阶段 2：执行引擎（第 1 周，后 4 天）★ Java 优势区

**目标**：彻底理解「一张图怎么被执行的」。这是你建立信心的关键阶段。

**读**（按顺序，全部带行号）：
1. `main.py` 全文（启动流程 + `prompt_worker`）：先看本文 [§0 后的启动链路梳理] + [缺口三 §3.5]。
2. `execution.py`：
   - `PromptQueue`（`:1244`）—— 堆队列 + Condition，对应 `PriorityBlockingQueue`。
   - `validate_prompt`（`:1121`）—— 图校验，先读懂它返回什么。
   - `PromptExecutor.execute` / `execute_async`（`:724` / `:727`）—— 主循环。
   - 单节点 `execute`（`:436`）—— 查缓存 → 取输入 → 实例化 → 调 FUNCTION → 写缓存。
3. `comfy_execution/`：
   - `graph.py:21` `DynamicPrompt`、`:193` `ExecutionList`（拓扑溶解）、`:312` `complete_node_execution`。
   - `caching.py:82` `CacheKeySetInputSignature`（缓存键 = 祖先链签名）、`:116` 签名构造。
   - `validation.py:4` `validate_node_input`（类型校验）。
   - `jobs.py`（jobs 统一视图层）。
4. 配合 [`SOURCE_DIVE_CN.md` 第一部分](SOURCE_DIVE_CN.md)（执行引擎逐行导读）。

**动手实验**（核心）：
- 启动 ComfyUI，连一个最简单的图（Load Checkpoint → CLIPTextEncode×2 → KSampler → VAEDecode → SaveImage）。
- 跑两次，**只改 SaveImage 的 filename_prefix**：观察第二次日志——大部分节点被缓存跳过（发 `execution_cached`），只有 SaveImage 重算。**这就是增量缓存的肉眼可见证据。**
- 试 `--cache-none` 再跑，对比区别。
- 试 `POST /interrupt`（或前端 Cancel），观察 `InterruptProcessingException` 怎么打断。

**验收**：能画出「从 POST /prompt 到结果写回 history」的完整时序图（参考本文第二篇），能解释为什么「只改末端节点会复用缓存」。

---

### 阶段 3：节点系统 + 第一个自定义节点（第 2 周）★ Java 优势区

**目标**：理解节点的「字段约定 + 反射驱动 + 注册表」机制，并**亲手写一个能跑的节点**。

**读**：
1. 本文 [缺口三 §3.2-3.4] + [缺口一 §1.13]。
2. `nodes.py`：
   - `NODE_CLASS_MAPPINGS`（`:2049`）—— 全局注册表。
   - `init_extra_nodes`（`:2539`）→ `init_builtin_extra_nodes`（`:2373`，**硬编码文件列表**）/ `init_builtin_api_nodes`（`:2521`，glob 扫描）/ `init_external_custom_nodes`（`:2323`）→ `load_custom_node`（`:2227`）。
   - `CLIPTextEncode`（`:56`）、`SaveImage`（`:1643`，`OUTPUT_NODE=True`）、`LoadImage.IS_CHANGED`（`:1783`）。
3. `comfy_api/latest/_io.py` —— 所有数据类型定义（`IMAGE :420` / `MODEL :613` / `LATENT :448` 等）。
4. 教学范例：`custom_nodes/example_tutorial/image_nodes.py`（V1 经典风格）、`custom_nodes/example_node.py.example`（V3 版本化 API）。

**动手实验**（强烈推荐，零侵入练手）：在 `custom_nodes/my_first_node/__init__.py` 写一个最小节点，重启即生效：

```python
class HelloWorld:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"default": "hello"}),
            "count": ("INT", {"default": 1, "min": 1, "max": 10}),
        }}
    RETURN_TYPES = ("STRING",)        # 返回元组，长度 = RETURN_TYPES 长度
    FUNCTION = "run"                  # 执行方法名字符串
    CATEGORY = "my/first"             # 前端分类

    def run(self, text, count):
        return (text * count,)        # 注意返回元组

NODE_CLASS_MAPPINGS = {"HelloWorld": HelloWorld}
NODE_DISPLAY_NAME_MAPPINGS = {"HelloWorld": "Hello World (My First)"}
```

重启后前端双击搜 `HelloWorld`，能在画布上看到你的节点、连线、运行。

**进阶实验**：
- 给节点加 `OUTPUT_NODE = True`，让它能作为终点独立执行。
- 实现 `IS_CHANGED` 返回 `float("nan")`，观察它每次都重算。
- 让节点输出 `IMAGE`（用 `torch.zeros(1,64,64,3)` 当测试图），接 SaveImage 出图。

**验收**：能解释「一个节点 = 一个带约定字段的类 + 一个注册字典」，能说出 `FUNCTION`/`RETURN_TYPES`/`OUTPUT_NODE`/`IS_CHANGED` 各自的作用，并能独立写出带 IMAGE 输入输出的节点。

---

### 阶段 4：AI 内核 —— 模型加载 + 采样（第 3 周）★★ 深度学习攻坚区

**目标**：看懂 `comfy/` 里的核心 AI 代码。这是最硬的阶段，**先确保本文「缺口二」的 10 节都吃透**。

**先决条件**：补深度学习直觉。推荐 3Blue1Brown 的「神经网络」系列视频（建立直觉），以及 PyTorch 官方 60 分钟入门（动手跑几个 tensor 操作）。

**读**（按调用链顺序，全部带行号）：
1. **模型加载**（本文 [§2.3]）：
   - `CheckpointLoaderSimple.load_checkpoint`（`nodes.py:627`）→ `comfy.sd.load_checkpoint_guess_config`（`sd.py:1864`）→ `load_state_dict_guess_config`（`sd.py:1931`）。
   - `model_detection.model_config_from_unet`（架构识别，遍历 `supported_models.models`）。
   - `ModelPatcher` 构造（`sd.py:1982`）+ `model.load_model_weights`（`model_base.py:346`）。
2. **显存管理**（本文 [§2.4] + `ARCHITECTURE_CN.md §4.3`）：
   - `comfy/model_management.py`：`VRAMState`（`:43`）、`current_loaded_models`（`:610`）、`LoadedModel`（`:694`）、`load_models_gpu`（`:860`）、`free_memory`（`:816`）。
   - 用 `--lowvram` 启动，观察日志里模型如何在 CPU/GPU 间搬运。
3. **采样流程**（本文 [§2.7] + `ARCHITECTURE_CN.md §4.4`）：
   - `KSampler.sample`（`nodes.py:1606`）→ `common_ksampler`（`nodes.py:1555`）→ `comfy.sample.sample`（`sample.py:71`）→ `comfy.samplers.KSampler.sample`（`samplers.py:1424`）→ `CFGGuider.sample`（`samplers.py:1268`）→ `k_diffusion/sampling.py` 各采样器。
   - scheduler（给 sigmas 序列）与 sampler（消费 sigmas）的解耦。
4. **VAE**（本文 [§2.8]）：`comfy/sd.py:476` `VAE`、`:1095` decode、`:1191` encode。
5. **CLIP**（本文 [§2.9]）：`comfy/sd1_clip.py:716`、`clip_model.py:148`、`sd.py:386`。

**动手实验**：
- 在 `KSampler` 的 FUNCTION 里加一行 `logging.info(f"x dtype={x.dtype} device={x.device} shape={x.shape}")`（注意：用完删掉，别提交），观察潜空间张量的 dtype/device/shape。
- 对比 `--lowvram` / 默认 / `--highvram` 三种模式的显存占用（任务管理器看 GPU 显存）。
- 换不同 sampler（euler / dpmpp_2m）和不同 steps，看效果和耗时差异。

**验收**：能复述本文 [§2.10] 的文生图全程调用链，能解释「为什么生图在 latent 空间而非像素空间」「ModelPatcher 为什么是装饰器模式」「为什么用 inference_mode」。

---

### 阶段 5：进阶与二次开发（第 4 周）

**目标**：能做有实际价值的二次开发。

**选一个方向深入**（按兴趣）：

| 方向 | 入手点 | 参考 |
|---|---|---|
| 写一个有用的自定义节点（带前端 UI） | `custom_nodes/` + `WEB_DIRECTORY` + 前端 JS | `custom_nodes/example_tutorial/` |
| 支持一个新模型 | `comfy/ldm/<新模型>/` + `supported_models.py` 注册 + `model_detection.py` 检测签名 | `ARCHITECTURE_CN.md §5` |
| 加 HTTP/WS 接口 | `server.py`，复杂逻辑抽 `api_server/routes/` 或 `app/*_manager.py` | `ARCHITECTURE_CN.md §5` |
| 改采样/调度 | `comfy/samplers.py` / `sample.py` / `model_sampling.py` | `ARCHITECTURE_CN.md §5` |
| 应用层服务（DB/资产/用户） | `app/` 新建 manager + `alembic_db/` 迁移 | `ARCHITECTURE_CN.md §4.6` |
| 写一个云端 API 节点 | `comfy_api_nodes/nodes_<厂商>.py` | 参照 `nodes_openai.py` |

**整个过程中务必遵守 `AGENTS.md`**（本文附录 C 摘录了关键红线）。

**验收**：完成一个能跑、符合 `AGENTS.md` 规范的小改动（哪怕只是给某节点加一个合法的输入项）。

---

### 学习计划总览表

| 阶段 | 周次 | 主题 | Java 优势? | 核心产出 |
|---|---|---|---|---|
| 0 | 半天 | 环境跑通 | — | 本地能出图 |
| 1 | W1 上 | Python 速成 | 部分 | 能读懂 Python |
| 2 | W1 下 | 执行引擎 | ✅✅ | 画得出执行时序图 |
| 3 | W2 | 节点系统 | ✅✅ | 写出第一个自定义节点 |
| 4 | W3 | AI 内核 | ❌ | 复述文生图调用链 |
| 5 | W4 | 二次开发 | ✅ | 一个合规的小改动 |

> **心理预期**：阶段 2-3 你会进步飞快（纯软件工程，Java 功力直接迁移）；阶段 4 会慢下来（要补深度学习直觉），别气馁，这是正常的。**不要跳过阶段 2 直接看 AI 内核**——执行引擎是你理解「数据怎么在节点间流动」的地基。

---

## 第四篇 二次开发实战

### 4.1 最快练手路径：自定义节点

**步骤**（零侵入，不动 core）：
1. 复制 `custom_nodes/example_tutorial/` → `custom_nodes/my_node/`。
2. 改类名 / `FUNCTION` / `INPUT_TYPES` / `RETURN_TYPES` / `NODE_CLASS_MAPPINGS`。
3. 重启 `main.py`。
4. 前端双击搜索你的节点。

### 4.2 节点字段约定速查（V1 经典风格）

| 字段/方法 | 必需 | 作用 | Java 类比 |
|---|---|---|---|
| `INPUT_TYPES()` classmethod | ✅ | 声明输入（required/optional/hidden） | `@Bean` 构造参数元信息 |
| `RETURN_TYPES` | ✅ | 输出类型元组 | 返回值类型签名 |
| `FUNCTION` | ✅ | 执行方法名字符串 | 反射调用的入口方法名 |
| `CATEGORY` | ✅ | 前端分类路径（`a/b`） | 分组标签 |
| `OUTPUT_NODE = True` | — | 标记终点节点 | `@RequestMapping` 入口 |
| `RETURN_NAMES` | — | 输出端口显示名 | — |
| `IS_CHANGED()` | — | 缓存指纹 | `@Cacheable` key |
| `VALIDATE_INPUTS()` | — | 业务校验 | `@Valid` |
| `DESCRIPTION` | — | 悬停说明 | javadoc |

### 4.3 核心数据类型清单（节选）

| 类型名 | 语义 | 实际 Python 对象 |
|---|---|---|
| `IMAGE` | 一批 RGB 图 | `torch.Tensor [N,H,W,C]` float `[0,1]` |
| `LATENT` | 隐空间样本 | `dict{"samples": Tensor[N,C,H/8,W/8]}` |
| `CONDITIONING` | 提示词嵌入 | `list[tuple[Tensor, dict]]` |
| `MODEL` | 扩散模型 | `comfy.model_patcher.ModelPatcher` |
| `CLIP` | 文本编码器 | `comfy.sd.CLIP` |
| `VAE` | VAE 编解码器 | `comfy.sd.VAE` |
| `MASK` | 蒙版 | `torch.Tensor [N,H,W]` |
| `INT`/`FLOAT`/`STRING`/`BOOLEAN` | 标量 | `int`/`float`/`str`/`bool` |
| `*` | Any 通配 | 任意 |

> 完整清单见 `comfy_api/latest/_io.py`（80+ 类型，含 `CONTROL_NET`/`CLIP_VISION`/`AUDIO`/`VIDEO`/`HOOKS`/3D 文件类型等）。

### 4.4 带前端 UI 的节点

在 `__init__.py` 设 `WEB_DIRECTORY = "./js"`，框架把 `my_node/js/*.js` 映射到 `/extensions/my_node/`，前端自动加载。后端类负责「算」，JS 负责「画 UI + 交互」。≈ Spring Boot 的 `static/` 资源目录约定。

### 4.5 关键红线（摘自 `AGENTS.md`）

- **改动最小化**：触及最少文件、最窄路径；新增抽象仅在消除真实重复时。
- **层级边界**：`execution.py` 只消费 prompt 图 + 执行状态，**不应感知** workflow id / 前端 id / 持久化 id。
- **禁止联网**：core 不加任何遥测/上报/更新检查。
- **模型代码**：不加 `torch.no_grad`/`inference_mode` 包装、不加 freeze 开关、不做内存管理（归 `model_management`）；节点**不得直接 patch 模型**，必须经 ModelPatcher。
- **Python 风格**：保持 import 在模块级；不乱加 `try/except`；删死代码；注释精简有用。

---

## 附录

### 附录 A：Java ↔ Python 术语速查

| 概念 | Java | Python / ComfyUI |
|---|---|---|
| 文件/模块 | `.java` → 类 | `.py` → 模块；含 `__init__.py` 的目录 = 包 |
| 动态加载 | `Class.forName` + ClassLoader | `importlib`（`nodes.py:2227`） |
| 注解 + AOP | `@Annotation` + 拦截器 | 装饰器 `@`（高阶函数） |
| 异步 | `CompletableFuture` / 虚拟线程 | `async/await` + `asyncio` |
| try-with-resources | `try(...){}` | `with x:`（上下文管理器） |
| record（不可变） | `record` | `NamedTuple` |
| Lombok @Data | `@Data` | `@dataclass` |
| 接口（鸭子类型） | `interface` + `implements` | `Protocol`（不需 implements） |
| Optional | `Optional<T>` | `T \| None` / `Optional[T]` |
| static 单例 | `static` / `@Component` | 模块级全局变量（`nodes.py:2049`） |
| WeakReference | `WeakReference` | `weakref`（`model_management.py:703`） |
| Stream API | `.stream().filter().map()` | 推导式 `[x for x in xs if ...]` |
| 多继承 | ❌ 单继承 + 多接口 | ✅ 多继承 + MRO |
| checked exception | `throws` 强制声明 | ❌ 全是 unchecked |
| 反射调方法 | `Method.invoke` | `getattr(obj, name)(...)`（`execution.py:289`） |
| 函数式接口 | `@FunctionalInterface` + Lambda | 一等公民函数，可直接塞 dict |

### 附录 B：ComfyUI 文件地图（带阅读优先级）

| 优先级 | 文件 | 作用 | 阶段 |
|---|---|---|---|
| ⭐⭐⭐ | `main.py` | 启动入口 + worker 线程 | 2 |
| ⭐⭐⭐ | `execution.py` | 执行引擎（校验/队列/缓存/执行） | 2 |
| ⭐⭐⭐ | `nodes.py` | 内置节点 + 节点加载入口 | 2,3 |
| ⭐⭐⭐ | `comfy_execution/graph.py` | 图 + 拓扑溶解 | 2 |
| ⭐⭐⭐ | `comfy_execution/caching.py` | 缓存键与策略 | 2 |
| ⭐⭐ | `server.py` | HTTP/WebSocket 路由 | 2 |
| ⭐⭐ | `comfy_execution/validation.py` | 类型校验 | 2,3 |
| ⭐⭐ | `comfy_api/latest/_io.py` | 数据类型定义 | 3 |
| ⭐⭐ | `custom_nodes/example_tutorial/` | 教学节点（抄写起点） | 3 |
| ⭐ | `folder_paths.py` | 模型路径解析 | 3 |
| ⭐ | `comfy/sd.py` | 模型加载（checkpoint→ModelPatcher） | 4 |
| ⭐ | `comfy/model_management.py` | 显存/设备/加载管理 | 4 |
| ⭐ | `comfy/model_patcher.py` | ModelPatcher（模型装饰层） | 4 |
| ⭐ | `comfy/samplers.py` + `k_diffusion/sampling.py` | 采样去噪循环 | 4 |
| ⭐ | `comfy/model_base.py` + `comfy/ldm/` | 模型网络实现（nn.Module） | 4 |
| ⭐ | `app/` + `api_server/` | 应用层服务 + REST API | 5 |

> 进阶参考（非必读）：[`ARCHITECTURE_CN.md`](../ARCHITECTURE_CN.md)（全景）、[`SOURCE_DIVE_CN.md`](SOURCE_DIVE_CN.md)（逐行导读）、[`AGENTS.md`](../AGENTS.md)（开发规范）。

### 附录 C：深度学习术语表（Java 程序员版）

| 术语 | 一句话解释 | ComfyUI 位置 |
|---|---|---|
| Tensor | 多维数组，可住 GPU | `IMAGE=[N,H,W,C]`（`nodes.py:1681`） |
| nn.Module | 模型基类，`__init__` 装子模块，`forward` 算前向 | `comfy/model_base.py:153` |
| forward | 一次前向计算（输入→输出） | `openaimodel.py:837` |
| state_dict | 模型权重的 `dict[str,Tensor]` | `model_base.py:346` |
| device | 张量住 CPU 还是 GPU | `model_management.py:193` |
| dtype | 精度（fp32/fp16/fp8） | `model_management.py:1054` |
| inference_mode | 推理时不记录梯度 | `execution.py:747` |
| checkpoint | 训练好的模型文件（.safetensors） | `sd.py:1864` |
| latent | 压缩后的隐空间表示（64×64×4） | `LATENT` 类型 |
| VAE | latent ↔ 像素图 的编解码网络 | `sd.py:476` |
| UNet | 扩散模型的核心去噪网络 | `openaimodel.py` |
| sampler | 去噪循环的算法（euler/dpmpp） | `k_diffusion/sampling.py` |
| scheduler | 给出 sigmas 序列（噪声调度） | `samplers.py:1359` |
| CFG | 用正/负提示词引导生成 | `samplers.py:591` |
| conditioning | 文本编码后的引导向量 + 元数据 | `list[tuple[Tensor,dict]]` |
| CLIP | 文本/图像编码器 | `sd1_clip.py:716` |
| LoRA | 轻量权重增量（patch） | `ModelPatcher.patches` |
| ModelPatcher | 模型的可叠加可回滚装饰层 | `model_patcher.py:292` |
| VRAM offload | 模型在 CPU/GPU 间动态搬运 | `load_models_gpu :860` |

### 附录 D：常用命令与排错

```bash
# 启动
python main.py                              # 默认 http://127.0.0.1:8188
python main.py --listen 0.0.0.0 --port 8188 # 局域网访问
python main.py --lowvram                    # 显存不足（智能 offload）
python main.py --cpu                        # 无 GPU 强制 CPU
python main.py --preview-method auto        # 潜空间预览
python main.py --enable-manager             # 启用 ComfyUI-Manager
python main.py --disable-api-nodes          # 关闭云端 API 节点（纯离线）
python main.py --cache-none                 # 关闭增量缓存（对比实验用）
```

**排错**：
- `Torch not compiled with CUDA enabled` → `pip uninstall torch` 后用 cu130 命令重装。
- 端口占用 → `--port 8190`。
- OOM → `--lowvram` / `--novram`；动态显存需 PyTorch ≥ 2.8（启动日志有 `DynamicVRAM support detected and enabled`）。
- 节点导入失败 → 看启动日志 `IMPORT FAILED`，通常缺依赖。
- 自定义节点不生效 → 必须重启 `main.py`；放 `custom_nodes/` 下、不带 `.disabled` 后缀。
- `comfy_extras/nodes_xxx.py` 新增不生效 → 它是**硬编码文件列表**，必须登记到 `nodes.py:2384`。

---

> **最后**：这个项目的精华在于「**用软件工程的 DAG 引擎，优雅地编排混乱的 AI 模型生态**」。你的 Java 功力（并发模型、设计模式、分层架构）在执行引擎层完全通用；深度学习部分把它当成「一个调 forward 的黑盒库」来用即可。按计划走，4 周后你会发现这个项目远没有看起来那么吓人。
