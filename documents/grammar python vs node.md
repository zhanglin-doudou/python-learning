作为资深前端工程师转 Python，关键字（Keywords）的差异是你**最容易写错且最难 Debug** 的坑。表面看语法相似，但底层语义截然不同。

我把差异分成四类，直击要害：

---

### 1. 同名异义（最致命的思维陷阱）

这些词你在 Node 里天天见，但在 Python 里完全不是一个东西。

| 关键字 | Node.js (JS/TS) 含义 | Python 含义 | 后果与实操 |
| :--- | :--- | :--- | :--- |
| **`async`/`await`** | **隐式事件循环**。调用`async`函数立即返回`Promise`，微任务自动入队。 | **显式协程**。调用`async`函数只返回`coroutine`对象，**必须**用`asyncio.run()`或`await`驱动，否则静默不执行。 | Node 里漏写`await`报`UnhandledRejection`；Python 里漏写只报`RuntimeWarning`，代码跳过不跑。 |
| **`import`/`from`** | **静态提升（Hoisting）**。`import`在编译阶段执行，模块依赖形成图，循环依赖靠`exports`透传。 | **运行时执行**。`import`语句按顺序执行，循环依赖会报`ImportError`（除非用局部导入或`typing.TYPE_CHECKING`规避）。 | Node 可随意在任意位置`import`；Python 必须严格写在文件顶部或函数内部，否则报错。 |
| **`None`/`null`** |`null`是**原始类型**（表示空值指针）。 |`None`是**单例对象**（`NoneType`），所有`None`指向同一内存地址。 | 判断时必须用`is None`（身份比较），用`==`虽偶尔正确但不符合规范。 |
| **`class`** | 单继承 + **原型链**（方法在原型上）。 | **多继承** + **MRO (C3 线性化)**。方法解析顺序由算法决定，不是简单的链式查找。 | 多重继承下调用`super()`会按 MRO 顺序执行所有父类，容易写出匪夷所思的调用链。 |

---

### 2. Python 独有（Node 里没有的概念）

| 关键字 | 作用 | Node 对应方案 |
| :--- | :--- | :--- |
| **`with`** | **上下文管理器**。自动管理资源（文件、锁、数据库连接），进入和退出时执行`__enter__`/`__exit__`。 | 需要手动`try/catch/finally`或在回调里`close()`。 |
| **`yield`** / **`yield from`** | 生成器函数，支持**惰性求值**和**协程**（简化迭代器实现）。 | JS 有`function*`和`yield`，但 Python 的`yield`还可以接收`send()`值（双向通信），比 JS 强大。 |
| **`pass`** | **空占位符**。语法上必须填代码块的地方（如空函数、空类），用`pass`跳过。 | Node 用空对象`{}`或直接省略。 |
| **`nonlocal`** / **`global`** | 显式声明变量作用域。在内嵌函数中修改外层（非全局）变量，必须`nonlocal`；修改模块级变量，必须`global`。 | Node 基于**词法作用域链**，内部函数可直接修改外层变量（除非用`let`/`const`块作用域限制），无需显式声明。 |
| **`del`** | 删除变量引用或对象属性，触发引用计数减 1。 | Node 用`delete`删除对象属性，但无法删除变量（`var a=1; delete a`无效）。 |
| **`raise`** | 主动抛出异常。 | Node 用`throw`。 |
| **`assert`** | 调试断言，条件为`False`时抛`AssertionError`。 | Node 也有`assert`，但 Python 的`assert`是**内置关键字**（而非库函数），运行时可加`-O`优化全部忽略。 |

---

### 3. Node 独有（Python 中无直接对应）

| 关键字 | Node (JS/TS) | Python 替代方案 |
| :--- | :--- | :--- |
| **`var`** / **`let`** / **`const`** | 声明变量，有块级作用域和 TDZ（暂时性死区）。 | Python **没有声明关键字**。直接赋值即声明（`a = 1`）。作用域只有**函数级**（`def`）和**模块级**，无块级作用域（`if`/`for`内赋值的变量会泄露到外层）。 |
| **`function`** / **`=>`** | 定义函数。 | Python 用 **`def`** 定义，箭头函数（lambda）仅限单表达式：`lambda x: x+1`。 |
| **`new`** | 构造对象实例。 | Python 用 **`__init__`** 方法，调用类时直接`MyClass()`（隐式调用`__new__`再`__init__`），极少直接写`new`。 |
| **`typeof`** / **`instanceof`** | 运行时类型检查。 | Python 用 **`type(obj)`** 或 **`isinstance(obj, cls)`**。 |
| **`switch`** / **`case`** | 多分支选择。 | Python 3.10+ 引入 **`match`/`case`**（结构模式匹配），比 JS 的`switch`强大得多，支持解构。 |
| **`export`** / **`default`** | 模块导出。 | Python 无导出关键字，模块内的所有变量默认可导入（`from module import *`），用`__all__`控制暴露列表。 |

---

### 4. 运算符与逻辑关键字（写法差异）

| Node.js | Python | 核心区别 |
| :--- | :--- | :--- |
|`&&`/`\|\|`/`!`| **`and`** / **`or`** / **`not`** | Python 是**英文单词**，不是符号。注意优先级：`and`低于`not`，`or`低于`and`。 |
|`===`/`==`| **`is`** / **`==`** | **这是新手第一爆雷点**：Python 的`==`比较**值**（类似 JS 的`==`但无隐式类型转换），`is`比较**内存地址**（类似 JS 的`===`对于引用类型）。字符串驻留会让`'a' is 'a'`为`True`，但计算出来的字符串必须用`==`。 |
|`? :`（三元） | **`a if condition else b`** | 顺序完全相反。Node 写`condition ? a : b`，Python 写`a if condition else b`。 |
|`&&=`/`\|\|=`| **无** | Python 没有逻辑赋值运算符（3.8+ 有`:=`海象运算符，但用于赋值表达式）。 |
|`!==`| **`is not`** | 否定身份比较，写`a is not None`（不是`a is !None`）。 |
|`++`/`--`| **无** | Python 没有自增/自减运算符，必须写`i += 1`。 |

---

### 📌 给 Node 老手的快速自查表（面试 / 写代码前必看）

| 你要做的事情 | Node.js 写法 | Python 写法 | 关键词陷阱 |
| :--- | :--- | :--- | :--- |
| 声明常量 |`const a = 1`|`a = 1`（无关键字） | 忘记 Python 无`const`|
| 判断空值 |`if (a === null)`|`if a is None`| 用了`==`而非`is`|
| 逻辑与 |`if (a && b)`|`if a and b`| 用了`&`或`&&`|
| 循环索引 |`for (let i=0; i<arr.length; i++)`|`for i, item in enumerate(arr):`| 写了`for i in range(len(arr))`不够 Pythonic |
| 类型检查 |`typeof a === 'string'`|`isinstance(a, str)`| 用了`type(a) == str`（不推荐） |
| 抛出错误 |`throw new Error('msg')`|`raise Exception('msg')`| 写了`throw`|
| 捕获错误 |`try/catch(e)`|`try/except Exception as e`| 写了`catch`|
| 删除属性 |`delete obj.prop`|`del obj.prop`| 写了`delete`|

---

### 💡 最后的建议

**忘掉 JS 的`let`和`const`**——Python 里赋值即声明，作用域只认函数和模块。**时刻默念`is`比`==`更重要**（判断`None`、`True`、`False`时）。遇到报错先检查是否用了 JS 习惯的关键字（`catch`、`throw`、`const`），这是 90% 新手报错的原因。😉