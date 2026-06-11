# 在QEMU 11里把Intel HAXM加速器捡回来

## 注意
本文人肉编写完之后让GPT优化了一下，看起来就很奇怪，不过总比我自己的原始写法靠谱  
只能说感觉哪哪都很奇怪，但之前写的版本因为没有commit丢了，又不想重新写，就这样了

## 前言
这次来点比较复古的老东西`Intel HAXM`  

这个东西现在其实已经没有人维护了  
Intel自己不维护了，上游QEMU在8.1版本也早就删了  
Windows上正常人应该优先考虑WHPX，Linux上肯定是KVM启动  

但是有些时候，WHPX这玩意也不是想开就开，用TCG又太慢了  
最近有一个复刻指定硬件平台拓扑的想法，所以顺手做了

本篇记录的就是在QEMU 11代码树里恢复`-accel hax`的过程  
记录怎么把上游删掉的HAX加速器重新塞回当前QEMU  

最后做到的效果是：

```shell
build/qemu-system-x86_64.exe -accel help
```

能看到：

```text
Accelerators supported in QEMU binary:
tcg
hax
whpx
```

然后机器开启hax时可以正常单核启动、多核启动，可以使用seabios、edk2-x86_64-code.fd启动。

比如说如下这样
```
vvvvvvvv@DESKTOP-VVVVVVV MINGW64 /d/git/GreenDamTan/qemu
#  ./build/qemu-system-x86_64.exe --version
QEMU emulator version 11.0.1 (v11.0.1-42-g83a651d5b0-dirty)
Copyright (c) 2003-2026 Fabrice Bellard and the QEMU Project developers

vvvvvvvv@DESKTOP-VVVVVVV MINGW64 /d/git/GreenDamTan/qemu
#  ./build/qemu-system-x86_64.exe -machine q35 -m 512 -cpu Skylake-Client-noTSX-IBRS -accel whpx
D:\git\GreenDamTan\qemu\build\qemu-system-x86_64.exe: -accel whpx: WHPX: No accelerator found, hr=00000000
D:\git\GreenDamTan\qemu\build\qemu-system-x86_64.exe: -accel whpx: failed to initialize whpx: No space left on device

vvvvvvvv@DESKTOP-VVVVVVV MINGW64 /d/git/GreenDamTan/qemu
#  ./build/qemu-system-x86_64.exe -machine q35 -m 512 -cpu Skylake-Client-noTSX-IBRS -accel hax
HAX is working and emulator runs in fast virt mode.
NOTE: HAX is deprecated and will be removed in a future release.
      Use 'whpx' (on Windows) or 'hvf' (on macOS) instead.
```


## 为什么还要恢复HAXM，而不是qemu-gvm
之前有一个叫qemu-gvm的项目  
https://github.com/qemu-gvm/qemu-gvm

这东西配合  
https://github.com/google/android-emulator-hypervisor-driver

据说是可以在AMD平台跑，而不仅是Intel，但我既然用Intel了干嘛关心AMD  
谁爱用谁谁买，存粹是个人偏好罢了

## 最初我是怎么评估工作量的
上游QEMU删除HAXM后，`-accel hax`就没了  

一开始我以为大概就是：

```text
恢复 target/i386/hax/
加回 meson.build
修一下 include
编译结束
```

后来发现太天真了  
它不是一个单独目录能解决的问题  
构建系统、CPU线程、BQL、内存映射、q35内存窗口、VAPIC option ROM、HLT exit、SMP CPUID，全都要碰  

尤其是SMP CPUID  
那个坑不是QEMU API变了导致的  
而是HAXM驱动本身的老设计导致的，所以你看我全程都是加`-cpu Skylake-Client-noTSX-IBRS`  
用`-cpu host`会炸上天的

## 恢复前先看上游删了什么

这个部分主要是为了确定恢复范围  
不然很容易只恢复`target/i386/hax/`，然后后面一路遇到运行时问题  

### 上游删除提交
先看上游是在哪删的  

```text
commit b91b0fc1635544341b9d00d1addc8ddf48e5b389
Author: Philippe Mathieu-Daudé <philmd@linaro.org>
Subject: accel: Remove HAX accelerator
```

这个提交删了HAX加速器本体，也删了外围入口  
不要只盯着`target/i386/hax/`看  

它大概涉及这些东西：

```text
target/i386/hax/
include/sysemu/hax.h
accel/stubs/hax-stub.c
accel/Kconfig
meson.build
meson_options.txt
qemu-options.hx
system/vl.c
hw/i386/pc_q35.c
hw/intc/apic_common.c
include/system/hw_accel.h
docs/about/removed-features.rst
```

这类恢复有个基本原则  
先不要大改  
先把旧代码尽量原样捡回来  
然后再根据当前QEMU API做最小适配  

不然你一边恢复一边重构，最后黑屏的时候根本不知道是旧代码本来就不行，还是自己改炸了  

### 把旧文件拉回来
旧文件可以从删除提交反向取  

```shell
git show --full-index --binary b91b0fc163 \
  -- accel/stubs/hax-stub.c \
     include/sysemu/hax.h \
     target/i386/hax/hax-accel-ops.c \
     target/i386/hax/hax-accel-ops.h \
     target/i386/hax/hax-all.c \
     target/i386/hax/hax-i386.h \
     target/i386/hax/hax-interface.h \
     target/i386/hax/hax-mem.c \
     target/i386/hax/hax-posix.c \
     target/i386/hax/hax-posix.h \
     target/i386/hax/hax-windows.c \
     target/i386/hax/hax-windows.h \
     target/i386/hax/meson.build |
git apply --3way --reverse
```

如果工作区当时已经有别的改动，千万别看到冲突或者状态乱了就直接`git reset --hard`  
这种恢复旧代码的活，文件多，而且有些路径还要移动  
手一快就容易把自己原本没打算动的东西一起带走  

如果只是想把暂存区清一下，可以用普通`git reset`  
这个不会删工作区内容  

```shell
git reset
```

然后再慢慢看：

```shell
git status --short
```

这个过程里我基本上是边恢复边查文件  
不要想着一次性全部套完  
因为旧代码里很多include路径已经不对了  
直接套完开编译，也只是获得一大屏报错  

这里有个立即要改的地方  
旧路径是：

```text
include/sysemu/hax.h
```

当前树里要放成：

```text
include/system/hax.h
```

QEMU这些年把不少`sysemu`头迁到了`system`下面  
这个不改的话后面到处include炸  

恢复后的HAX文件大概是这些：

```text
accel/stubs/hax-stub.c
include/system/hax.h
target/i386/hax/hax-accel-ops.c
target/i386/hax/hax-accel-ops.h
target/i386/hax/hax-all.c
target/i386/hax/hax-i386.h
target/i386/hax/hax-interface.h
target/i386/hax/hax-mem.c
target/i386/hax/hax-posix.c
target/i386/hax/hax-posix.h
target/i386/hax/hax-windows.c
target/i386/hax/hax-windows.h
target/i386/hax/meson.build
```

这些文件的作用也比较清楚  

| 文件 | 大概作用 |
| --- | --- |
| `hax-all.c` | HAX核心逻辑，初始化、vCPU创建、run loop、exit处理 |
| `hax-mem.c` | 内存映射、MemoryListener、RAM block notifier |
| `hax-accel-ops.c` | 当前QEMU CPU加速器线程入口 |
| `hax-accel-ops.h` | HAX vCPU线程和同步函数声明 |
| `hax-windows.c` | Windows下打开HAXM驱动、DeviceIoControl、APC kick |
| `hax-posix.c` | Darwin/NetBSD一类POSIX路径，open/ioctl/mmap |
| `hax-interface.h` | HAXM ioctl ABI结构 |
| `hax-i386.h` | HAX内部结构汇总 |
| `meson.build` | 把HAX源文件接进i386 system target |

注意`hax-interface.h`里的结构不要为了好看乱改  
这些东西对着驱动ABI  
改错了编译能过，运行时也会给你一点颜色看看  

这里还有个小习惯  
恢复这种旧后端时，我会先把文件清单固定住  
后面遇到链接错误或者运行问题，至少知道哪些文件理论上应该存在  

```shell
find target/i386/hax -maxdepth 1 -type f | sort
```

大概应该有：

```text
target/i386/hax/hax-accel-ops.c
target/i386/hax/hax-accel-ops.h
target/i386/hax/hax-all.c
target/i386/hax/hax-i386.h
target/i386/hax/hax-interface.h
target/i386/hax/hax-mem.c
target/i386/hax/hax-posix.c
target/i386/hax/hax-posix.h
target/i386/hax/hax-windows.c
target/i386/hax/hax-windows.h
target/i386/hax/meson.build
```

如果后面链接时提示某些`hax_*`符号不存在  
先别急着怀疑代码没恢复  
也可能只是平台文件没编进去  

### 不只恢复HAX目录
这里单独拎出来说一下  
因为这是恢复这种被删除功能时最容易想少的地方  

我一开始也是先盯着：

```text
target/i386/hax/
```

但上游删除HAX时，不只是把这个目录删了  
很多非HAX目录里也有HAX相关判断  
这些东西漏掉后，编译可能还是能过，但运行时会出各种莫名其妙的问题  

后来我按删除提交把外围项重新扫了一遍  
大概要看这些：

```text
MAINTAINERS
docs/about/build-platforms.rst
docs/about/deprecated.rst
docs/about/index.rst
docs/about/removed-features.rst
docs/system/index.rst
docs/system/introduction.rst
hw/i386/pc_q35.c
hw/intc/apic_common.c
include/hw/core/cpu.h
include/system/hw_accel.h
system/cpus.c
system/vl.c
```

这里面不是每个都必须按旧版本原样恢复  
但是至少要知道它当年为什么存在  

比如：

| 文件 | 说明 |
| --- | --- |
| `hw/i386/pc_q35.c` | q35 legacy VGA lowmem窗口 |
| `hw/intc/apic_common.c` | KVM VAPIC option ROM排除HAX |
| `system/vl.c` | board初始化后的hax_sync_vcpus |
| `include/system/hw_accel.h` | hwaccel_enabled()要认识HAX |
| `system/cpus.c` | 旧Windows SleepEx逻辑现在挪到HAX线程 |
| `include/hw/core/cpu.h` | vcpu_dirty注释里本来提到HAX |

文档类文件就看当前分支定位  
比如`docs/about/removed-features.rst`里如果还写着：

```text
HAXM (``-accel hax``) (removed in 8.2)
```

那当前这个分支既然已经恢复HAXM，就不应该继续说它removed  
直接删掉这段更合理  
不要改成“已恢复”  
因为`removed-features.rst`本来就不是用来记录分支魔改的  

`MAINTAINERS`如果要补，也要注意旧路径：

```text
include/sysemu/hax.h
```

现在应该写：

```text
include/system/hax.h
```

这种路径如果不改，后面别人按MAINTAINERS找文件也会很奇怪  

## 先让它能进构建系统

这一阶段只追求一件事  
让`./configure --enable-hax`能正常进入配置系统，并且最终`x86_64-softmmu`目标知道自己有`CONFIG_HAX`  

不要急着跑guest  
配置入口没打通时，后面所有运行验证都没有意义  

### 第一轮：configure不认识hax
源码放回来后第一件事肯定是让配置系统认识它  

如果只恢复源码，不加Meson option，直接：

```shell
./configure --enable-hax
```

会得到类似：

```text
ERROR: Unknown option: "hax"
```

这个比较好修  
先在`meson_options.txt`加回feature option：

```meson
option('hax', type: 'feature', value: 'auto',
       description: 'HAX acceleration support')
```

然后`scripts/meson-buildoptions.sh`里要有帮助项：

```shell
printf "%s\n" '  hax             HAX acceleration support'
```

还要有解析项：

```shell
--enable-hax) printf "%s" -Dhax=enabled ;;
--disable-hax) printf "%s" -Dhax=disabled ;;
```

这一步修完之后，至少`./configure --help | grep -i hax`应该能看到东西了  

能看到类似：

```text
# ./configure --help | grep -i hax
  hax             HAX acceleration support
```

如果这里没有，那就不用继续往下编译了  
`--enable-hax`都还没进configure入口，后面肯定不会有`CONFIG_HAX`  

这里顺便说一句  
QEMU这个`scripts/meson-buildoptions.sh`有时候是生成文件  
如果后续跑了更新build options的命令，记得回头确认`--enable-hax`没有被冲掉  

### 第二轮：让CONFIG_HAX进target
仅仅有option还不够  
还要让`CONFIG_HAX`进入accelerators  

顶层`meson.build`里要把HAX绑定到x86 system emulator：

```meson
accelerator_targets += {
  'CONFIG_HAX': ['i386-softmmu', 'x86_64-softmmu'],
}
```

HAX不要加到user target  
也不要加到别的架构  
这东西就是i386/x86_64系统模拟器的加速器，甚至这东西只有Intel能用  

然后是平台可用性判断：

```meson
if get_option('hax').allowed()
  if get_option('hax').enabled() or host_os in ['windows', 'darwin', 'netbsd']
    accelerators += 'CONFIG_HAX'
  endif
endif

if 'CONFIG_HAX' not in accelerators and get_option('hax').enabled()
  error('HAX not available on this platform')
endif
```

这里保留了老QEMU的行为  
Windows、Darwin、NetBSD上auto可以开  
如果有人以后只想显式`--enable-hax`才打开，那可以再改策略  
但要同步改文档，不然以后排障时又要怀疑是不是Meson判断错了  

摘要也加上：

```meson
summary_info += {'HAX support': config_all_accel.has_key('CONFIG_HAX')}
```

这样configure最后能直接看到：

```text
HAX support                     : YES
```

如果配置摘要里是`NO`，也别急着看HAX源码  
先看Meson有没有把`CONFIG_HAX`放进`accelerators`  

可以粗暴点：

```shell
grep -n "CONFIG_HAX\|HAX support\|get_option('hax')" meson.build
```

这里应该能看到三类东西：

```text
accelerator_targets里有CONFIG_HAX
get_option('hax')逻辑会把CONFIG_HAX加入accelerators
summary_info里有HAX support
```

如果只加了option，没有加入`accelerators`，那configure当然不会把它当可用加速器  

还有一个小入口是`accel/Kconfig`  
这里也要恢复：

```kconfig
config HAX
    bool
```

这个地方不需要写平台条件  
平台是否允许由Meson控制  
Kconfig这边保持和其它加速器同级就行  

另外`include/exec/poison.h`里也要把`CONFIG_HAX`放回去：

```c
#pragma GCC poison CONFIG_HAX
```

这个看起来像没用  
但QEMU里这种poison是为了防止普通代码随便直接判断`CONFIG_HAX`  
应该通过`hax_enabled()`这类运行时接口判断的地方，就不要直接碰config符号  

用户可见帮助文本也别忘了  
`qemu-options.hx`里supported accelerators列表要有`hax`：

```text
supported accelerators are kvm, xen, hax, hvf, nitro, nvmm, whpx, mshv or tcg
```

否则就会出现一种很蠢的情况  
`-accel help`里有hax  
但是`qemu-system-x86_64 -help`文本里没写  

这种问题不影响运行  
但以后别人看到帮助文本又会以为这个分支没恢复HAX  

### 第三轮：i386目录要把hax拉进来
`target/i386/meson.build`里需要两件事  

```meson
i386_ss.add(when: 'CONFIG_HAX', if_true: files('host-cpu.c'))
subdir('hax')
```

`host-cpu.c`这个别漏  
HAX这类x86硬件加速器会用到host CPU model相关支持  

HAX自己的`meson.build`最后是这样：

```meson
i386_system_ss.add(when: 'CONFIG_HAX', if_true: files(
  'hax-all.c',
  'hax-mem.c',
  'hax-accel-ops.c',
))

if host_os == 'windows'
  i386_system_ss.add(when: 'CONFIG_HAX', if_true: files('hax-windows.c'))
else
  i386_system_ss.add(when: 'CONFIG_HAX', if_true: files('hax-posix.c'))
endif
```

这里我踩过一个坑  
不要写成这样：

```meson
i386_system_ss.add(when: ['CONFIG_HAX', 'CONFIG_WIN32'],
                   if_true: files('hax-windows.c'))
```

看起来很合理对吧  
Windows就编`hax-windows.c`  

但这里不行  
`CONFIG_WIN32`是host config符号，存在于`config-host.h`  
而这里source set的`when`按target config解析  

结果就是`hax-windows.c`没进目标库  
最后链接时你会看到一串：

```text
undefined reference to `hax_mod_open'
undefined reference to `hax_vcpu_run'
undefined reference to `hax_sync_vcpu_state'
undefined reference to `hax_kick_vcpu_thread'
```

这种错误第一眼很像函数没实现  
实际上实现就在`hax-windows.c`里，只是它根本没被编进去

## 再修QEMU 11 API变化

配置入口通了之后，就进入比较枯燥的编译修复阶段  
这部分没什么玄学，基本就是旧HAX代码和当前QEMU API对不上  

先放一个对照表  
后面遇到报错基本都在这里面：

| 旧写法 | 当前写法 | 说明 |
| --- | --- | --- |
| `#include "sysemu/..."` | `#include "system/..."` | 很多sysemu头迁到了system命名空间 |
| `include/sysemu/hax.h` | `include/system/hax.h` | HAX公共头路径迁移 |
| `exec/address-spaces.h` | `system/address-spaces.h` | address space头路径迁移 |
| `hw/boards.h` | `hw/core/boards.h` | Machine相关头路径变化 |
| `cpu->env_ptr` | `cpu_env(cpu)` | `CPUState`不再暴露`env_ptr`字段 |
| `qemu_mutex_lock_iothread()` | `bql_lock()` | 当前BQL接口 |
| `qemu_mutex_unlock_iothread()` | `bql_unlock()` | 当前BQL接口 |
| `qemu_wait_io_event(cpu)` | `qemu_process_cpu_events(cpu)` | 当前vCPU线程循环写法不同 |
| `cpu_physical_memory_rw()` | `address_space_rw()` | 合并读写helper已经没了 |
| `class_init(ObjectClass *, void *)` | `class_init(ObjectClass *, const void *)` | `TypeInfo.class_init`签名变化 |
| `init_machine(MachineState *)` | `init_machine(AccelState *, MachineState *)` | `AccelClass.init_machine`签名变化 |

### include路径大迁移
QEMU 8.1附近的代码拿到QEMU 11里，最先炸的是include  

这个阶段基本就是编译一次，炸一批头文件  
修掉再编译，再炸下一批  

比较常见的是：

```text
fatal error: exec/address-spaces.h: No such file or directory
fatal error: hw/boards.h: No such file or directory
```

这些基本都属于路径迁移  

```text
include/sysemu/hax.h      -> include/system/hax.h
exec/address-spaces.h     -> system/address-spaces.h
hw/boards.h               -> hw/core/boards.h
```

比如遇到：

```text
fatal error: exec/address-spaces.h: No such file or directory
```

就不要去找是不是少恢复了文件  
当前树里已经是：

```c
#include "system/address-spaces.h"
```

遇到：

```text
fatal error: hw/boards.h: No such file or directory
```

就改成：

```c
#include "hw/core/boards.h"
```

这种错误基本没什么技术含量  
但是它多  
而且容易让人误以为恢复文件不完整  

`hax-all.c`里最后补了这些头：

```c
#include "accel/accel-ops.h"
#include "exec/cpu-common.h"
#include "system/address-spaces.h"
#include "hw/core/boards.h"
#include "hw/i386/apic.h"
#include "qemu/atomic.h"
#include "qemu/thread.h"
```

几个头文件：

| 头文件 | 为什么需要 |
| --- | --- |
| `accel/accel-ops.h` | `AccelClass`完整定义 |
| `exec/cpu-common.h` | `cpu_physical_memory_read/write()`声明 |
| `system/address-spaces.h` | `address_space_memory`、`address_space_io` |
| `hw/core/boards.h` | `MachineState`完整定义 |
| `hw/i386/apic.h` | APIC base和TPR相关接口 |
| `qemu/atomic.h` | `qatomic_read()`、`qatomic_set()` |
| `qemu/thread.h` | `QemuMutex` |

`hax-mem.c`里要有：

```c
#include "system/address-spaces.h"
#include "system/memory.h"
#include "system/ramlist.h"
```

不然会出现：

```text
invalid use of incomplete typedef 'MemoryRegionSection'
variable 'hax_memory_listener' has initializer but incomplete type
unknown type name 'RAMBlockNotifier'
```

`hax-accel-ops.c`里要有：

```c
#include "accel/accel-cpu-ops.h"
```

不然会遇到：

```text
invalid use of incomplete typedef 'AccelOpsClass'
implicit declaration of function 'ACCEL_OPS_CLASS'
```

还有一种是函数或者结构明明存在，但是因为只拿到了前向声明，所以报“不完整类型”  
这种通常不是函数没了，而是include不够  

比如：

```text
invalid use of incomplete typedef 'AccelClass'
```

补：

```c
#include "accel/accel-ops.h"
```

比如：

```text
invalid use of incomplete typedef 'MemoryRegionSection'
```

补：

```c
#include "system/memory.h"
```

比如：

```text
unknown type name 'RAMBlockNotifier'
```

补：

```c
#include "system/ramlist.h"
```

只能说后续版本头文件拆得更细了  
狠狠的补就完事了

### CPUState没有env_ptr了
旧代码里常见这种写法：

```c
CPUArchState *env = cpu->env_ptr;
```

当前不行  
会直接报：

```text
error: 'CPUState' has no member named 'env_ptr'
```

现在用：

```c
CPUArchState *env = cpu_env(cpu);
```

这类修改没什么技术含量，搜索替换就完事了

### AccelClass签名变了
旧HAX里的`init_machine`签名也不对  

旧的像这样：

```c
static int hax_accel_init(MachineState *ms)
```

当前`AccelClass.init_machine`要的是：

```c
static int hax_accel_init(AccelState *as, MachineState *ms)
```

不改会看到类似：

```text
assignment to 'int (*)(AccelState *, MachineState *)'
from incompatible pointer type 'int (*)(MachineState *)'
```

还有`TypeInfo.class_init`现在是：

```c
class_init(ObjectClass *, const void *)
```

旧代码如果还是`void *`，也要顺手改掉  

### vCPU线程模型不能照搬旧HAX
这个地方比include麻烦  
旧HAX vCPU线程大概长这样：

```c
qemu_mutex_lock_iothread();

do {
    if (cpu_can_run(cpu)) {
        hax_smp_cpu_exec(cpu);
    }
    qemu_wait_io_event(cpu);
} while (!cpu->unplug || cpu_can_run(cpu));
```

当前QEMU里`qemu_mutex_lock_iothread()`这一套已经不是这个写法  
要换成BQL接口  

主循环抄KVM/WHPX/NVMM：

```c
bql_lock();

do {
    qemu_process_cpu_events(cpu);

    if (cpu_can_run(cpu)) {
        r = hax_smp_cpu_exec(cpu);
        if (r == EXCP_DEBUG) {
            cpu_handle_guest_debug(cpu);
        }
    }
} while (!cpu->unplug || cpu_can_run(cpu));

bql_unlock();
```

进入HAXM真正执行guest前要释放BQL  
回来处理MMIO、PIO、中断、状态同步时再拿回来  

```c
bql_unlock();
cpu_exec_start(cpu);
hax_ret = hax_vcpu_run(vcpu);
cpu_exec_end(cpu);
bql_lock();
```

这个逻辑如果搞反了，很容易死锁或者事件处理不对  

### handle_interrupt断言
一开始把线程入口接回来后，启动时还会遇到这个：

```text
ERROR:../system/cpus.c:697:cpus_register_accel: assertion failed: (ops->handle_interrupt)
Bail out! ERROR:../system/cpus.c:697:cpus_register_accel: assertion failed: (ops->handle_interrupt)
```

当前`AccelOpsClass`要求`create_vcpu_thread`和`handle_interrupt`都存在  
旧HAX只注册了vCPU线程和kick回调  

补上：

```c
ops->handle_interrupt = generic_handle_interrupt;
```

放在`hax_accel_ops_class_init()`里  
KVM、WHPX、NVMM这些非TCG加速器也这么干  

### Windows APC唤醒
Windows HAX路径里有`QueueUserAPC()`  
以前`qemu_wait_io_event()`里会有：

```c
SleepEx(0, TRUE);
```

现在vCPU loop改了，这个消费APC的调用也要移到HAX线程里  
不然kick vCPU时排进去的dummy APC可能没人处理  

大概是这样：

```c
do {
    qemu_process_cpu_events(cpu);

#ifdef _WIN32
    SleepEx(0, TRUE);
#endif

    if (cpu_can_run(cpu)) {
        r = hax_smp_cpu_exec(cpu);
        ...
    }
} while (!cpu->unplug || cpu_can_run(cpu));
```

这里还有个竞态  
`hax_start_vcpu_thread()`里不能在`qemu_thread_create()`返回后马上访问`cpu->accel`  
因为`cpu->accel`是在子线程`hax_init_vcpu(cpu)`里创建的  

错误写法大概是：

```c
qemu_thread_create(...);
assert(cpu->accel);
cpu->accel->hThread = qemu_thread_get_handle(cpu->thread);
```

这个会遇到：

```text
ERROR:../target/i386/hax/hax-accel-ops.c:76:hax_start_vcpu_thread: assertion failed: (cpu->accel)
```

正确做法是放到子线程里，`hax_init_vcpu(cpu)`之后，`cpu_thread_signal_created(cpu)`之前：

```c
static void *hax_cpu_thread_fn(void *arg)
{
    CPUState *cpu = arg;

    hax_init_vcpu(cpu);

#ifdef _WIN32
    cpu->accel->hThread = qemu_thread_get_handle(cpu->thread);
#endif

    cpu_thread_signal_created(cpu);
    ...
}
```

另外不要在HAX里重新分配`cpu->thread`和`cpu->halt_cond`  
当前CPU core已经初始化好了  
你再搞一套，`qemu_cpu_kick()`和vCPU线程等待可能就不在同一个对象上了  

### 平台文件也不能只顾Windows
这次实际验证主要在Windows，但后续这套东西会移植到PVE上  
所以最容易只盯着`hax-windows.c`看  

但HAX旧代码还有POSIX路径：

```text
hax-posix.c
hax-posix.h
```

Darwin和NetBSD这这平台真有HAXM吗？  
但根据旧实现编译入口确实不能写死只支持Windows  

所以HAX平台层大概保持这种形态：

```text
hax-all.c      调通用逻辑
hax-windows.c  Windows下打开HAXM设备、DeviceIoControl、APC kick
hax-posix.c    POSIX下open/ioctl/mmap
```

像`HAX_VCPU_IOCTL_SET_CPUID`这种新增入口，也不要只在Windows里补  
头文件声明和POSIX实现也要跟上  
不然某个平台一编译就会缺符号  

当然POSIX路径没有实测，所以这里不能写得太满  
只能说代码层面跟着接口迁移了  
真实Darwin/NetBSD跑起来还要重新验证，反正我没有，我不跑哈哈  

Windows这边比较特殊的是kick vCPU  
它会用`QueueUserAPC()`  
所以前面才要在vCPU线程里保留`SleepEx(0, TRUE)`  
否则HAXM那边被踢了，QEMU线程这边不消费APC，也会出现唤醒不及时的问题  

### 把HAX放回硬件加速器判断
这个属于外围项  
但是不能漏  

`include/system/hw_accel.h`里要引入：

```c
#include "system/hax.h"
```

然后判断硬件加速器时加上HAX：

```c
return hvf_enabled()
    || hax_enabled()
    || kvm_enabled()
    || nvmm_enabled()
    || whpx_enabled();
```

漏了这个，有些非HAX目录里的逻辑就不会把HAX当硬件加速器  
后面排障就很玄学  

### system/vl.c里的同步也要恢复
旧HAX在board初始化后有一段同步：

```c
if (hax_enabled()) {
    /* FIXME: why isn't cpu_synchronize_all_post_init enough? */
    hax_sync_vcpus();
}
```

这段现在放回`qemu_init_board()`里`realtime_init()`之后  
同时include改成：

```c
#include "system/hax.h"
```

这东西看起来像历史包袱  
但恢复旧后端时，不建议随便删  
先保留老路径，确认能跑起来后再说  

## 运行起来之后开始排障

能编译和能进入`-accel help`，只能说明后端注册成功  
真正麻烦的是跑起来之后的黑屏、卡死、慢、无响应  

这一阶段我基本不相信窗口表现  
能用HMP/QMP取证就尽量取证  

先列一下后面几个问题的关系：

| 现象 | 最后定位 | 关键判断 |
| --- | --- | --- |
| q35窗口全黑但guest在跑 | `smm-ranges`没保留，VGA lowmem没覆盖RAM | `pmemsave 0xb8000`有文本，`screendump`全黑 |
| HAX加载KVM VAPIC option ROM | `apic_common.c`缺少`!hax_enabled()` | guest停在奇怪option ROM路径附近 |
| 固件取指或state-change异常 | ROMD没有映射给HAX | 日志能看到ROMD region被忽略 |
| SeaBIOS停在HLT | `HAX_EXIT_HLT`回来没同步真实寄存器 | TCG同位置能继续推进，HAX不动 |
| OVMF 4 vCPU卡死 | CPUID leaf 1 APIC ID串了 | 1/2 vCPU正常，4 vCPU不稳 |
| Linux 4 vCPU极慢 | 全程串行`hax_vcpu_run()` | serial继续动，但Web/SSH很久没响应 |

### q35黑屏：不是没跑，是显示内存没路由过去
能编译之后，终于可以跑了  
然后马上遇到q35黑屏  

命令大概是：

```shell
build/qemu-system-x86_64.exe \
  -accel hax \
  -machine q35 \
  -cpu qemu64 \
  -m 512M
```

表现是图形窗口全黑  
一开始很容易以为guest根本没跑  
但是用HMP看寄存器，发现其实已经推进到SeaBIOS等待点附近了：

```text
(qemu) info registers
```

```text
CS=f000 EIP=0000b757
```

再去看`0xb8000`，甚至能看到SeaBIOS写进去的文本  
但是`screendump`全黑  

至于为什么是`0xb8000`因为彩色文本模式的显存就是`0xb8000`那边，你问我我问谁
至于非得看qemu怎么写的话就去`hw/display/vga.c`自己看`vga_update_memory_access`

可以这样保存一小段物理内存：

```text
(qemu) pmemsave 0xb8000 0x1000 b8000.bin
```

然后随便用十六进制工具看  
能看到类似`Boot failed`这种SeaBIOS文本  

图形这边可以用：

```text
(qemu) screendump screen.ppm
```

如果导出来全是0，那就说明显示设备那边没收到文本写入  

这就说明不是BIOS没跑  
而是VGA文本缓冲没进显卡那条路径  

继续看`info mtree -f`  

```text
(qemu) info mtree -f
```

问题出来了  
`0xa0000-0xbffff`落在`pc.ram`里，而不是`vga-lowmem`

正常应该是：

```text
00000000000a0000-00000000000bffff (prio 1, i/o): vga-lowmem
```

原因在q35  
当前q35通过`smm-ranges`创建`smram-region`  
legacy VGA低端窗口又是靠这个region路由到PCI address space，然后让`vga-lowmem`覆盖RAM  

HAX不支持真正SMM，所以：

```c
x86_machine_is_smm_enabled(x86ms)
```

在HAX下是false  
如果这个false直接传给q35 host bridge的`smm-ranges`，`smram-region`不创建，VGA lowmem窗口就没了  

修法不是打开SMM  
HAX没有完整SMM执行能力  
这里只是要保留range container，让VGA lowmem能覆盖RAM  

所以改成：

```c
smm_enabled = x86_machine_is_smm_enabled(x86ms);
/*
 * HAX does not support SMM execution, but q35's low legacy PCI window is
 * routed through smram-region.  Keep the range container so VGA legacy
 * memory (0xa0000-0xbffff) can override RAM while leaving SMI disabled.
 */
smm_ranges = smm_enabled || hax_enabled();
object_property_set_bool(phb, PCI_HOST_PROP_SMM_RANGES,
                         smm_ranges, NULL);
```

但是LPC设备这里还是用真正的`smm_enabled`：

```c
qdev_prop_set_bit(lpc_dev, "smm-enabled", smm_enabled);
```

不要把`smm-enabled`也改成`smm_ranges`  
否则就是给guest暴露HAX根本不支持的SMI/SMM路径  

这个地方最容易犯的错误就是  
看见`smm_ranges = smm_enabled || hax_enabled()`  
就顺手把下面LPC设备也改成`smm_ranges`  

这样短期可能看起来没问题  
但语义完全错了  

```text
smm-ranges   只是保留q35内存窗口
smm-enabled  是真的给guest开SMM/SMI路径
```

HAX这里只需要前者，不需要后者  

### KVM VAPIC option ROM也会扑街
另一个黑屏相关点在`hw/intc/apic_common.c`  

旧HAX删除前，这里创建KVM VAPIC option ROM时有HAX排除条件  
恢复时也要补回来：

```c
if (!vapic && s->vapic_control & VAPIC_ENABLE_MASK &&
        !hax_enabled() && current_machine->ram_size >= 1024 * 1024) {
    vapic = sysbus_create_simple("kvmvapic", -1, NULL);
}
```

如果漏掉，HAX启动时可能会加载KVM VAPIC option ROM  
然后guest停在一些很奇怪的位置  
甚至能在附近看到`kvm aPiC`字符串

### ROMD region不能再忽略
旧HAX代码会忽略ROMD region  
以前可能没事，现在不行  

现在固件经常以ROMD region形式出现在guest物理地址空间  
如果HAX不映射这些区域，vCPU取指就可能直接出问题  
然后触发HAX state-change exit  

旧日志大概会看到这种方向：

```text
Ignoring ROMD region 0x00000000ffc84000->0x0000000100000000
Ignoring ROMD region 0x00000000000e0000->0x0000000000100000
```

所以`hax_process_section()`里不能只接受RAM  
要接受RAM和ROMD：

```c
/* We only care about RAM and ROMD regions */
if (!memory_region_is_ram(mr) && !memory_region_is_romd(mr)) {
    return;
}
if (memory_region_is_romd(mr)) {
    warn_report("Mapping ROMD region 0x%016" PRIx64 "->0x%016" PRIx64,
                start_pa, start_pa + size);
}
```

映射时如果是ROM或者ROMD，加只读标志：

```c
if (memory_region_is_rom(section->mr) || memory_region_is_romd(section->mr)) {
    flags |= HAX_RAM_INFO_ROM;
}
```

HAXM接口本来就有`HAX_RAM_INFO_ROM`  
所以这里不需要改驱动ABI  

### fast MMIO接口也变了
旧HAX里还有这个：

```c
cpu_physical_memory_rw(hft->gpa, &hft->value, hft->size, hft->direction);
```

当前树里这个helper已经没了  
换成：

```c
address_space_rw(&address_space_memory, hft->gpa,
                 MEMTXATTRS_UNSPECIFIED, &hft->value, hft->size,
                 hft->direction);
```

HAX API v4里`direction == 2`表示GPA到GPA2的MMIO move  
这段旧逻辑保留：

```c
cpu_physical_memory_read(hft->gpa, &value, hft->size);
cpu_physical_memory_write(hft->gpa2, &value, hft->size);
```

这里不用想太多  
普通MMIO走`address_space_rw`  
特殊move按原来的逻辑搬  

### HLT exit不同步状态会卡住
解决q35 lowmem后，HAX q35 + SeaBIOS还有一个卡住问题  

现象是：

```shell
build/qemu-system-x86_64.exe \
  -accel hax \
  -machine q35 \
  -cpu qemu64 \
  -m 512M \
  -display none
```

采样寄存器  
HAX 3秒和10秒都停在类似：

```text
EIP=000108bc HLT=1
```

TCG同样参数，3秒也会到这个位置  
但是10秒后就推进到BIOS等待点了  

这说明HAX不是完全没跑  
而是HLT唤醒路径用错状态了  

HAX vCPU run期间不会一直更新QEMU侧`CPUArchState`  
`HAX_EXIT_HLT`回来后，如果不先同步真实HAX寄存器状态，外层判断中断和HLT状态时用的是旧的`env->eflags`、`env->eip`、段状态  

于是加同步：

```c
case HAX_EXIT_HLT:
    /*
     * HAX does not keep CPUArchState current while the vCPU runs.
     * Pull the real state back before the next outer-loop interrupt
     * check, otherwise halt wakeup decisions can be made using stale
     * flags and segment state from reset/firmware entry.
     */
    hax_vcpu_sync_state(env, 0);
    if (!(cpu->interrupt_request & CPU_INTERRUPT_HARD) &&
        !(cpu->interrupt_request & CPU_INTERRUPT_NMI)) {
        /* hlt instruction with interrupt disabled is shutdown */
        env->eflags |= IF_MASK;
        cpu->halted = 1;
        cpu->exception_index = EXCP_HLT;
        ret = 1;
    }
    break;
```

这个改完以后，q35 + SeaBIOS就能从早期HLT继续往后走  

### state-change exit要让外层知道该停
HAX还有个`HAX_EXIT_STATECHANGE`  
旧代码会打印：

```text
VCPU shutdown request
```

这个打印我保留了，虽然会刷屏  
但它对排查guest是不是触发state-change很有用  

不过当前vCPU loop里不能只设置一个局部返回值然后继续绕  
要让外层循环知道该处理系统请求  

所以现在是：

```c
case HAX_EXIT_STATECHANGE:
    fprintf(stdout, "VCPU shutdown request\n");
    qemu_system_shutdown_request(SHUTDOWN_CAUSE_GUEST_SHUTDOWN);
    hax_vcpu_sync_state(env, 0);
    cpu->exception_index = EXCP_INTERRUPT;
    ret = 1;
    break;
```

`HAX_EXIT_UNKNOWN_VMEXIT`和default exit也类似  
请求reset后，同步状态，dump一下，然后设置`EXCP_INTERRUPT`返回外层  

不然有时候你会看到它重复进入run loop，日志刷得起飞，但主循环没机会正常处理shutdown/reset  

### 当时怎么排障
这里补一下实际排障时用到的一些方法  
不然只看上面的结论，会觉得像是凭空猜出来的  

HAX这种后端问题，很多时候图形窗口没有意义  
窗口黑不代表guest没跑  
窗口亮了也不代表状态完全正确  

我主要靠这些东西看：

```text
HMP info registers
HMP info mtree -f
QMP screendump
HMP pmemsave
-serial stdio
```

启动时可以顺手开一个monitor  
比如：

```shell
build/qemu-system-x86_64.exe \
  -accel hax \
  -machine q35 \
  -cpu qemu64 \
  -m 512M \
  -monitor stdio
```

如果还要跑serial，就分开：

```shell
build/qemu-system-x86_64.exe \
  ... \
  -serial stdio \
  -monitor telnet:127.0.0.1:4444,server,nowait
```

然后另开一个终端连进去：

```shell
telnet 127.0.0.1 4444
```

看寄存器：

```text
(qemu) info registers
```

看内存树：

```text
(qemu) info mtree -f
```

保存物理内存：

```text
(qemu) pmemsave 0xb8000 0x1000 b8000.bin
```

保存屏幕：

```text
(qemu) screendump screen.ppm
```

这些东西比盯着一个黑窗口靠谱得多  

比如q35黑屏那次，关键不是“它黑了”  
而是这几个现象同时出现：

```text
info registers 显示已经到 CS=f000 EIP=0000b757 附近
screendump 全黑
pmemsave 0xb8000 能看到 SeaBIOS 文本
info mtree -f 显示 0xa0000-0xbffff 是 pc.ram
```

这四个放一起，基本就能排除“BIOS没执行”  
因为BIOS文本都写到`0xb8000`了  
问题是写进了普通RAM，没有写到VGA lowmem窗口  

所以后面才去看q35的`smm-ranges`  

HLT那个问题也类似  
如果只是看窗口黑，很难判断  
但是对比HAX和TCG的寄存器采样就很明显：

```text
HAX  3秒: EIP=000108bc HLT=1
HAX 10秒: EIP=000108bc HLT=1
TCG  3秒: EIP=000108bc HLT=1
TCG 10秒: CS=f000 EIP=0000b757
```

这说明SeaBIOS到这个HLT点本身不是问题  
TCG也会经过这里  
问题是HAX没有被正确唤醒继续往后走  

再结合HAX run期间不持续同步`CPUArchState`  
就能定位到`HAX_EXIT_HLT`回来后要先`hax_vcpu_sync_state()`  

SMP CPUID那次也不是一上来就知道是CPUID  
中间也怀疑过APIC、中断、OVMF、ROMD、VAPIC  
最后靠现象收敛：

```text
1 vCPU 能进
2 vCPU 能进
4 vCPU 固件阶段不稳定
AP能收到INIT/SIPI
卡在MP初始化后续路径
Linux也卡在SMP相关早期启动附近
```

这类现象很像APIC ID或者拓扑错了  
再去看HAXM的CPUID实现，就看到它的CPUID缓冲不是per-vCPU  

还有Linux SMP慢的问题  
最开始它不是完全不动  
`-serial stdio`能看到systemd继续启动  
HMP里寄存器也在Linux kernel高地址区  
但是Web/SSH半天没响应  

这就不像“卡死”而是“跑得非常慢”  

最后回头看自己把`hax_vcpu_run()`全程锁住了  
4个vCPU轮流进HAXM  
那就解释得通了  

排这种问题的时候，不要只盯着最后一个报错  
尤其HAX这种老加速器，有时候根本不会给你一个像样的报错  
只能靠几个侧面现象拼出来  

## SMP CPUID：最恶心的一段
前面的东西虽然多，但基本都是顺着错误修  
真正折磨人的是SMP CPUID  

最开始的现象是OVMF多核不稳定  

命令类似：

```shell
build/qemu-system-x86_64.exe \
  -accel hax \
  -machine q35 \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=build/pc-bios/edk2-x86_64-code.fd \
  -m 2048 \
  -smp 4,sockets=1,cores=4,threads=1 \
  -cpu Skylake-Client-noTSX-IBRS
```

1 vCPU、2 vCPU能进TianoCore  
4 vCPU开始就容易黑屏或者停在固件占位画面  

换成Linux guest也很典型  
GRUB能显示，内核能开始加载，然后卡在：

```text
Booting `FNOS GNU/Linux'
Loading Linux 6.18.18-trim ...
Loading initial ramdisk ...
```

串口没后续kernel日志  
Web hostfwd不通  
SSH也没有banner  

一开始会怀疑APIC、中断、内存映射、OVMF本身  
后来绕了一圈，问题落到CPUID leaf 1  

这里中间其实走了不少弯路  

先怀疑ROMD，是因为固件代码如果没映射，vCPU确实会莫名其妙掉出来  
所以先把ROMD映射补上  
补完之后固件取指这类问题少了，但4 vCPU OVMF还是不稳  

再怀疑KVM VAPIC，是因为旧HAX删除前就有排除`kvmvapic`的逻辑  
这个补上后，q35某些黑屏问题也少了  
但OVMF 4 vCPU还是会卡  

再看APIC路径  
AP不是完全没起来  
调试OVMF MP mailbox时能看到AP已经收到了INIT/SIPI  
如果AP完全没收到IPI，那会更像中断注入或者APIC路径问题  
但它收到后又在后面卡住，就更像AP身份或者拓扑被识别错  

这时候再看CPUID leaf 1，就比较对味了  
因为leaf 1 EBX高8位就是APIC ID  
而HAXM又刚好共享CPUID缓冲  

所以这个问题不是靠一个报错定位出来的  
是前面几个可能项排掉后，现象慢慢逼到CPUID上的  

### HAXM的CPUID缓冲不是per-vCPU
这里是关键  
HAXM驱动内部不是给每个vCPU一份独立CPUID  
它只给vCPU 0分配`guest_cpuid`  
其它vCPU共享这一份缓冲  

但是CPUID leaf 1的`EBX[31:24]`是initial APIC ID  
每个vCPU都不同  

也就是说，如果多个vCPU并发进入HAXM  
共享CPUID缓冲里可能刚好是别的vCPU的APIC ID  
固件做MP初始化时就会把AP认错  

这种错非常讨厌  
它不是一上来就报错  
而是AP收到INIT/SIPI之后，在后面某个MP初始化路径里卡住  

更烦的是，HAXM没有CPUID VM exit  
`struct hax_tunnel`里没有“guest执行CPUID了”这种exit reason  
QEMU没法在guest真正执行CPUID那一瞬间按当前vCPU切leaf 1  

能做的只有进入`hax_vcpu_run()`之前先刷CPUID  

### 先支持HAX_CAP_CPUID
HAXM capability里有`HAX_CAP_CPUID`  
先识别这个能力：

```c
hax->supports_cpuid = !!(cap->winfo & HAX_CAP_CPUID);
```

然后Windows和POSIX路径都要实现`HAX_VCPU_IOCTL_SET_CPUID`入口  
否则QEMU侧构好了CPUID表也下不去  

这里不展开ioctl细节  
反正核心就是能把：

```c
struct hax_cpuid
```

传给HAXM驱动  

### 第一次错误修法：完整下发QEMU CPUID
最容易想到的修法是：  
既然HAXM CPUID会乱，那我把QEMU生成的CPUID完整传进去不就好了  

然后就炸了  

尤其是：

```text
-accel hax -cpu Skylake-Client-noTSX-IBRS -smp 1
```

固件阶段能进  
Linux启动内核后panic  

原因是HAXM默认CPUID路径本来会做兼容性过滤  
具体可以参考`https://github.com/intel/haxm/blob/master/core/cpuid.c`  
它不是原样使用QEMU CPU model  

比如新的Intel family 6 model会被回退：

```c
if (display_family == 0x6 && display_model > 0x1f) {
    eax = 0x000106f1;
}
```

EBX低24位也会固定成比较保守的值：

```c
ebx = (0x01 << 16) | (0x08 << 8) | 0x00;
```

ECX还会加上`HYPERVISOR`：

```c
ecx = (ecx & hax_supported_ecx) | CPUID_EXT_HYPERVISOR;
```

如果把完整QEMU CPUID塞进去，相当于绕过了HAXM原本的保护  
Linux可能打开一些HAXM根本没法可靠模拟的CPU/PMU路径  

所以完整下发不行  
尤其不能让单vCPU也走`SET_CPUID`  

这里其实有个很容易误判的点  
SMP CPUID问题发生在多核  
但如果修法不对，最先炸的反而可能是单核Linux  

原因就是单核原本根本不需要我们干预CPUID  
HAXM默认路径虽然老，但是它知道自己能模拟什么  
它会主动把一些新CPU特性压下去  

而我们一旦对单核也调用`SET_CPUID`，就等于告诉HAXM：

```text
别用你自己的默认过滤了
按QEMU这个CPU model来
```

对`Skylake-Client-noTSX-IBRS`这类CPU model来说，这就很危险  
Linux看到的特性更多，可能初始化PMU或者其它HAXM不可靠支持的路径  
然后kernel panic  

所以后面判断一个CPUID修法对不对，不能只看4 vCPU能不能进OVMF  
还要回头看：

```text
-smp 1 Linux还能不能启动
```

这个很重要  
因为很容易修好多核固件，顺手把单核内核启动搞坏  

### 正确方向：SMP只下发leaf 1
后面策略改成：

```text
单vCPU：不调用SET_CPUID，继续走HAXM默认CPUID
SMP：只下发CPUID leaf 1
leaf 1：只修APIC ID/topology相关内容
```

先处理EAX  
保持HAXM默认行为，新Intel family 6 model回退到老model：

```c
static uint32_t hax_cpuid_1_eax(uint32_t eax)
{
    uint32_t family = ((eax >> 8) & 0xf);
    uint32_t model = ((eax >> 4) & 0xf);
    uint32_t ext_model = ((eax >> 16) & 0xf);
    uint32_t ext_family = ((eax >> 20) & 0xff);
    uint32_t display_family = family == 0xf ? family + ext_family : family;
    uint32_t display_model = (family == 0x6 || family == 0xf) ?
                             (ext_model << 4) | model : model;

    /*
     * Match HAXM's default CPUID path.  Newer Intel model IDs can make guest
     * kernels initialize host PMU paths that HAXM cannot emulate correctly.
     */
    if (display_family == 0x6 && display_model > 0x1f) {
        return 0x000106f1;
    }

    return eax;
}
```

构造leaf 1时，只保留EBX里的APIC ID  
低24位用HAXM默认值：

```c
entry = hax_cpuid_add_entry(cpuid, &cpuid_i, 0x00000001, 0);
hax_cpuid(env, 0x00000001, 0, entry);
apic_id = entry->ebx & 0xff000000;
entry->eax = hax_cpuid_1_eax(entry->eax);
entry->ebx = apic_id | (0x01 << 16) | (0x08 << 8);
```

这里也踩过坑  
如果保留QEMU EBX低24位，q35 + Linux + 4 vCPU会继续停在`Loading initial ramdisk ...`  

所以不要觉得QEMU算出来的就一定更正确  
对HAXM这种老驱动来说，保守才是正确  

ECX/EDX也只保留HAXM默认支持范围  
ECX最后补`HYPERVISOR`：

```c
entry->ecx &= CPUID_EXT_SSE3 |
              CPUID_EXT_SSSE3 |
              CPUID_EXT_SSE41 |
              CPUID_EXT_SSE42 |
              CPUID_EXT_CX16 |
              CPUID_EXT_MOVBE |
              CPUID_EXT_AES |
              CPUID_EXT_PCLMULQDQ |
              CPUID_EXT_POPCNT |
              CPUID_EXT_XSAVE |
              CPUID_EXT_AVX |
              CPUID_EXT_F16C;
entry->ecx |= CPUID_EXT_HYPERVISOR;
```

设置CPUID时明确跳过单vCPU：

```c
static int hax_vcpu_set_cpuid(CPUArchState *env)
{
    g_autofree struct hax_cpuid *cpuid = NULL;
    size_t size;

    if (!hax_global.supports_cpuid || hax_global.vm->numvcpus <= 1) {
        return 0;
    }

    size = sizeof(*cpuid) +
           HAX_MAX_CPUID_ENTRIES * sizeof(struct hax_cpuid_entry);
    cpuid = g_malloc0(size);
    cpuid->total = hax_build_cpuid(env, cpuid);

    return hax_set_cpuid(env, cpuid);
}
```

这段很重要  
不要为了看起来统一，把`numvcpus <= 1`这个判断删了  

这里还有一个细节  
为什么只下发leaf 1，不顺手把其它leaf也补一下  

因为我们真正要修的是：

```text
leaf 1 EBX[31:24] initial APIC ID
```

这是固件MP初始化最直接依赖的东西  
HAXM没有per-vCPU CPUID缓冲，导致这个字段容易串  

其它leaf虽然也可能和拓扑有关  
但完整模拟现代x86 CPUID拓扑不是这次恢复HAXM要做的事情  
一旦把范围扩大，很容易绕过HAXM默认过滤  
最后又回到单核Linux panic那类问题  

所以这里宁愿保守  
只动当前确定有问题的leaf 1  

这个策略看起来有点怂  
但是对已经停更的HAXM来说，怂是优点  
不要把guest引到HAXM没有能力兜住的路径上去  

### 第二次错误修法：全程串行SET_CPUID+RUN
既然HAXM的CPUID缓冲是VM-wide共享  
那为了避免别的vCPU抢着改leaf 1，最稳的办法就是锁住：

```text
SET_CPUID + hax_vcpu_run()
```

也就是一个vCPU设置好自己的leaf 1后，马上进入HAXM执行  
这期间不允许其它vCPU改CPUID缓冲  

固件阶段这样确实有效，OVMF多核能继续往后走  
Linux也不再卡死在`Loading initial ramdisk ...`  

但是进入Linux后性能就极差了  

实际观察：

```text
4 vCPU启动比1 vCPU更慢
300秒内Web 127.0.0.1:5666超时
SSH hostfwd能建立TCP连接，但读不到banner
HMP info registers显示已经在64-bit Linux kernel高地址区运行
```

这就说明不是固件阶段卡死了  
而是Linux SMP阶段被我们锁成了轮流跑  

4个vCPU实际变成一个一个进HAXM那当然慢  

### 最终策略：早期串行，进内核后并行
HAXM没有CPUID exit  
所以没法做真正精确的“只在CPUID指令附近加锁”  

只能做阶段性策略：

```text
固件/bootloader阶段：锁住SET_CPUID + RUN
Linux kernel阶段：只锁SET_CPUID，RUN恢复并行
```

用一个全局标记：

```c
static QemuMutex hax_cpuid_run_mutex;
static bool hax_cpuid_run_locked = true;
```

判断什么时候进入kernel  
这里用的是一个启发式：

```c
static bool hax_vcpu_in_kernel(CPUState *cpu, CPUArchState *env)
{
    /*
     * HAXM's CPUID buffer is shared across vCPUs, so firmware and bootloader
     * SMP bring-up need SET_CPUID + RUN serialization to keep APIC IDs stable.
     * Once the BSP is executing a 64-bit high-half kernel, stop serializing the
     * whole RUN ioctl; otherwise HAX SMP becomes slower than one vCPU.
     */
    return cpu->cpu_index == 0 &&
           (env->hflags & HF_CS64_MASK) &&
           env->eip >= 0xffff800000000000ULL;
}
```

判断条件是：

```text
BSP
64-bit代码段
RIP进入canonical high-half地址
```

这不是HAXM ABI  
只是针对x86_64 Linux比较实用的判断  
OVMF、GRUB、Linux decompressor、trampoline大多还在低地址  
正式kernel起来后通常进入高半区  

run loop里早期这样：

```c
if (qatomic_read(&hax_cpuid_run_locked)) {
    qemu_mutex_lock(&hax_cpuid_run_mutex);
    /*
     * HAXM keeps guest CPUID in a VM-wide buffer shared by all APs.
     * During firmware and bootloader SMP bring-up, serialize the run
     * itself so a vCPU cannot execute CPUID with another vCPU's APIC
     * ID in leaf 1 EBX[31:24].
     */
    hax_ret = hax_vcpu_set_cpuid(env);
    if (!hax_ret) {
        hax_ret = hax_vcpu_run(vcpu);
    }
    qemu_mutex_unlock(&hax_cpuid_run_mutex);
}
```

进入kernel后这样：

```c
else {
    /*
     * After the BSP reaches the guest kernel, keeping the RUN ioctl
     * serialized makes SMP unusably slow.  Still serialize SET_CPUID
     * updates, but allow vCPUs to run in parallel afterwards.
     */
    qemu_mutex_lock(&hax_cpuid_run_mutex);
    hax_ret = hax_vcpu_set_cpuid(env);
    qemu_mutex_unlock(&hax_cpuid_run_mutex);
    if (!hax_ret) {
        hax_ret = hax_vcpu_run(vcpu);
    }
}
```

解除run锁放在HAX exit回来后  
先同步寄存器，再判断：

```c
case HAX_EXIT_INTERRUPT:
case HAX_EXIT_PAUSED:
    hax_vcpu_sync_state(env, 0);
    if (hax_smp_cpuid_enabled() && qatomic_read(&hax_cpuid_run_locked) &&
        hax_vcpu_in_kernel(cpu, env)) {
        qatomic_set(&hax_cpuid_run_locked, false);
    }
    break;
```

这套方案不优雅，但是能解决实际问题  

早期APIC ID不会乱，进入Linux后SMP也不会一直排队  
你说Windows？那我不知道嘻嘻。

## 构建与验证

前面修了这么多，最后还是要回到构建和运行验证  
这里我把构建命令、SeaBIOS、OVMF、Linux guest分开写  
因为它们覆盖的问题不一样  

### 构建命令
Windows构建一定要在MINGW64环境里  
不要拿普通MSYS shell硬上  
路径、pkg-config、Python、编译器都可能不一样  

我一般会先确认当前shell环境  
至少`gcc`、`python`、`pkg-config`这些应该来自MINGW64环境  

```shell
which gcc
which python
which pkg-config
```

如果输出跑到奇怪的MSYS路径或者Windows别的Python里  
那后面报错就很难看了  

我这里是：

```shell
cd /d/git/GreenDamTan/qemu
export CC="ccache gcc"
export CXX="ccache g++"
./configure --enable-gtk \
            --enable-sdl \
            --target-list=x86_64-softmmu \
            --disable-werror \
            --disable-capstone \
            --enable-hax \
            --prefix=/artifacts
meson compile -C build
```

配置摘要里要看到：

```text
Targets and accelerators
  HAX support                     : YES
  WHPX support                    : YES
  TCG support                     : YES
  target list                     : x86_64-softmmu
```

编译完成后会生成：

```text
build/qemu-system-x86_64.exe
build/qemu-system-x86_64w.exe
```

再检查加速器注册：

```shell
build/qemu-system-x86_64.exe -accel help
```

输出里要有：

```text
Accelerators supported in QEMU binary:
tcg
hax
whpx
```

如果没有hax，按顺序查：

```text
meson_options.txt有没有hax
scripts/meson-buildoptions.sh有没有--enable-hax
meson.build里CONFIG_HAX有没有进accelerators
accelerator_targets有没有绑定x86_64-softmmu
target/i386/meson.build有没有subdir('hax')
```

如果链接时怀疑平台文件没编进去，就查`build.ninja`：

```shell
grep -n "hax-all.c\|hax-mem.c\|hax-accel-ops.c" build/build.ninja
grep -n "hax-windows.c\|hax-posix.c" build/build.ninja
```

Windows下至少应该看到：

```text
hax-all.c
hax-mem.c
hax-accel-ops.c
hax-windows.c
```

如果只有前三个，没有`hax-windows.c`  
那后面大概率就是一串`undefined reference to hax_*`  

这时候别急着翻`hax-windows.c`里有没有函数  
先看Meson条件是不是写错了  

### 运行验证：q35 SeaBIOS
这个主要验证q35 VGA lowmem和HLT路径  

```shell
build/qemu-system-x86_64.exe \
  -accel hax \
  -machine q35 \
  -cpu qemu64 \
  -m 512M
```

修复前表现：

```text
窗口全黑
guest实际已经运行
pmemsave 0xb8000能看到SeaBIOS文本
info mtree -f里0xa0000-0xbffff是pc.ram
```

修复后重点看：

```text
info mtree -f里0xa0000-0xbffff是vga-lowmem
screendump不再全黑
寄存器能从EIP=000108bc HLT=1继续推进
```

### 运行验证：q35 OVMF SMP
这个主要验证固件阶段SMP CPUID  

```shell
build/qemu-system-x86_64.exe \
  -accel hax \
  -machine q35 \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=build/pc-bios/edk2-x86_64-code.fd \
  -m 2048 \
  -smp 4,sockets=1,cores=4,threads=1 \
  -cpu Skylake-Client-noTSX-IBRS
```

验证过：

```text
1 vCPU 显示TianoCore/PXE
2 vCPU 显示TianoCore/PXE
4 vCPU 显示TianoCore/PXE
```

这一步如果4 vCPU黑屏  
优先回头看CPUID leaf 1的APIC ID处理  
其次看`SET_CPUID + RUN`早期串行有没有生效  

### 运行验证：adl-n + fnOS/Linux
这个主要验证Linux SMP阶段  
尤其是“不要全程串行RUN”  

命令大概是：

```shell
build/qemu-system-x86_64.exe \
  -machine adl-n \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=build/pc-bios/edk2-x86_64-code.fd \
  -m 2048 \
  -smp 4,sockets=1,cores=4,threads=1 \
  -cpu Skylake-Client-noTSX-IBRS \
  -boot d \
  -accel hax \
  -serial stdio \
  ... fnOS nvme and netdev options ...
```

修复前会停在：

```text
Booting `FNOS GNU/Linux'
Loading Linux 6.18.18-trim ...
Loading initial ramdisk ...
```

或者能进kernel但慢得离谱  
Web 300秒还没响应  
SSH能建立TCP但没有banner  

最终验证结果：

```text
-smp 1 可以启动到登录画面
-smp 4 可以越过 Loading initial ramdisk ...
serial 输出进入 fnOS 登录界面
hostfwd Web 返回 HTTP 200
SSH 返回 SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u7
```

这说明单vCPU没有被CPUID修复带崩  
4 vCPU也没有被早期CPUID问题卡死  
进入Linux后RUN并行也恢复了  

### 最后保留的验证矩阵
这种改动不要只跑一个命令就说好了  
因为这几个问题互相之间有点牵制  

比如：

```text
q35 SeaBIOS        主要看lowmem和HLT
q35 OVMF 1 vCPU    主要看基础固件路径
q35 OVMF 4 vCPU    主要看固件SMP CPUID
Linux 1 vCPU       主要看不要破坏HAXM默认CPUID
Linux 4 vCPU       主要看SMP CPUID和RUN并行
```

我最后按这个矩阵看：

```text
SeaBIOS q35 qemu64 512M
  - 图形窗口不是全黑
  - info mtree -f里0xa0000-0xbffff是vga-lowmem
  - HLT后能继续推进到BIOS等待点

OVMF q35 Skylake-Client-noTSX-IBRS 1 vCPU
  - 能显示TianoCore/PXE

OVMF q35 Skylake-Client-noTSX-IBRS 2 vCPU
  - 能显示TianoCore/PXE

OVMF q35 Skylake-Client-noTSX-IBRS 4 vCPU
  - 能显示TianoCore/PXE

fnOS/Linux adl-n Skylake-Client-noTSX-IBRS 1 vCPU
  - 能启动到登录画面
  - 不触发CPUID相关kernel panic

fnOS/Linux adl-n Skylake-Client-noTSX-IBRS 4 vCPU
  - 能越过Loading initial ramdisk
  - serial能进登录界面
  - Web hostfwd返回HTTP 200
  - SSH hostfwd返回banner
```

如果只测OVMF 4 vCPU，很可能看不出单核Linux被CPUID搞坏  
如果只测Linux 1 vCPU，又看不出固件SMP APIC ID问题  
如果只测SeaBIOS，又看不出OVMF MP初始化问题  

所以最后至少要覆盖这几类  

尤其是改CPUID逻辑时，我建议每次都至少跑：

```text
OVMF q35 1 vCPU
OVMF q35 4 vCPU
Linux 1 vCPU
Linux 4 vCPU
```

这几个能过，才说明没有只修一头炸另一头  

## 常见错误整理
这里把几个比较典型的错误再列一下  
以后如果再往更新QEMU移植，可以直接对着查  

这一节就当速查  
因为这些错很多都不值得重新分析一遍  
看到现象直接往对应方向查就行  

### Unknown option: hax
**现象**

```text
ERROR: Unknown option: "hax"
```

**优先检查**

```text
meson_options.txt
scripts/meson-buildoptions.sh
```

**处理**

如果是旧build目录缓存，也可以重新configure  

### -accel help没有hax
**优先检查**

```text
配置摘要里HAX support是不是YES
meson.build里CONFIG_HAX有没有加入accelerators
accelerator_targets有没有绑定x86_64-softmmu
target-list是不是x86_64-softmmu或者i386-softmmu
```

### 找不到exec/address-spaces.h
**现象**

```text
fatal error: exec/address-spaces.h: No such file or directory
```

**处理**

```c
#include "system/address-spaces.h"
```

### 找不到hw/boards.h
**现象**

```text
fatal error: hw/boards.h: No such file or directory
```

**处理**

```c
#include "hw/core/boards.h"
```

### CPUState没有env_ptr
**现象**

```text
error: 'CPUState' has no member named 'env_ptr'
```

**处理**

```c
CPUArchState *env = cpu_env(cpu);
```

### AccelOpsClass不完整
**现象**

```text
invalid use of incomplete typedef 'AccelOpsClass'
implicit declaration of function 'ACCEL_OPS_CLASS'
```

**处理**

```c
#include "accel/accel-cpu-ops.h"
```

### AccelClass不完整
**现象**

```text
invalid use of incomplete typedef 'AccelClass'
```

**处理**

```c
#include "accel/accel-ops.h"
```

### undefined reference to hax_*
**现象**

```text
undefined reference to `hax_mod_open'
undefined reference to `hax_vcpu_run'
undefined reference to `hax_sync_vcpu_state'
undefined reference to `hax_kick_vcpu_thread'
```

**优先检查**

优先查`target/i386/hax/meson.build`  
尤其是Windows下`hax-windows.c`有没有进build.ninja  

**处理**

不要用`CONFIG_WIN32`做source set条件  

### cpus_register_accel断言handle_interrupt
**现象**

```text
ERROR:../system/cpus.c:697:cpus_register_accel: assertion failed: (ops->handle_interrupt)
```

**处理**

```c
ops->handle_interrupt = generic_handle_interrupt;
```

### hax_start_vcpu_thread断言cpu->accel
**现象**

```text
ERROR:../target/i386/hax/hax-accel-ops.c:76:hax_start_vcpu_thread: assertion failed: (cpu->accel)
```

**处理**

不要在`qemu_thread_create()`返回后访问`cpu->accel`  
把Windows的`hThread`保存放到子线程里，`hax_init_vcpu(cpu)`之后  

### q35黑屏但guest在跑
**现象**

```text
图形窗口全黑
info registers显示guest已经跑到BIOS后面
pmemsave 0xb8000能看到SeaBIOS文本
```

**优先检查**

优先查：

```text
info mtree -f
0xa0000-0xbffff是不是vga-lowmem
```

**处理**

如果是`pc.ram`，回头看`smm_ranges = smm_enabled || hax_enabled()`  

### SeaBIOS停在HLT
**现象**

```text
EIP=000108bc HLT=1
HAX长时间不推进
TCG同位置可以继续往后走
```

**处理**

优先看`HAX_EXIT_HLT`有没有先：

```c
hax_vcpu_sync_state(env, 0);
```

### Linux单核kernel panic
**现象**

```text
-accel hax -cpu Skylake-Client-noTSX-IBRS -smp 1
固件阶段能进
Linux kernel启动后panic
```

**优先检查**

优先看是不是单vCPU也调用了`SET_CPUID`  
单vCPU应该继续使用HAXM默认CPUID路径  

### Linux四核卡Loading initial ramdisk
**现象**

```text
Booting `FNOS GNU/Linux'
Loading Linux 6.18.18-trim ...
Loading initial ramdisk ...
```

**优先检查**

优先看：

```text
CPUID leaf 1 EBX[31:24] APIC ID
EBX低24位有没有按HAXM默认值
早期SET_CPUID + RUN有没有串行
```

### Linux四核能进但慢到不可用
**现象**

```text
serial能继续输出
HMP寄存器已经在Linux kernel高地址区
Web/SSH长时间没有实际响应
```

**处理**

优先看是不是全程锁住`hax_vcpu_run()`  
进入64-bit high-half kernel后应该只锁`SET_CPUID`，放开RUN并行  

## 还没验证的部分
这次主要验证的是Windows HAXM路径  
也就是`hax-windows.c`这条  

还没认真验证：

```text
Darwin HAX路径
NetBSD HAX路径
guest内部长时间多vCPU压力
dirty logging
migration
调试器相关路径
各种现代CPU model组合
```

所以这个恢复不能理解成“HAXM已经完全复活”  
更准确地说，是“在当前验证过的Windows场景里，QEMU 11重新具备了可用的HAX入口”  

HAXM项目本身已经停了  
如果遇到驱动打不开，还要先检查这些：

```text
HAXM驱动是否安装
BIOS/UEFI里VT-x是否开启
Hyper-V/WHPX/VBS/Credential Guard是否占用了VT-x
当前Windows版本是否还能正常加载HAXM驱动
运行权限是否足够
```

这不是纯QEMU侧能解决的问题  

## 后续维护建议
如果以后还要往更新的QEMU移植，我建议按这个顺序来：

```text
1. 先恢复target/i386/hax和include/system/hax.h
2. 加回Meson、Kconfig、configure入口
3. 让x86_64-softmmu先编译链接
4. 对照KVM/WHPX修vCPU线程和BQL
5. 接回system/vl.c、hw_accel.h这些外围入口
6. 处理q35 lowmem和KVM VAPIC排除
7. 处理ROMD、HLT、state-change exit
8. 最后再动SMP CPUID
```

不要一上来就重构HAX代码  
这东西本来就老，先让它按旧逻辑活起来  
能编译、能启动、能跑一个真实guest之后，再考虑有没有必要整理  

尤其不要随便改HAXM ioctl结构  
那些结构对着驱动ABI  
看着不顺眼也先忍着  

### 如果要拆提交
这种恢复我建议不要一个巨大提交全塞进去  
虽然自己本地折腾时经常会一坨改完  
但真要以后维护，最好拆开  

比较合理的拆法大概是：

```text
1. 恢复HAXM源码和基础入口
2. 适配QEMU 11 API和include路径
3. 适配vCPU线程、BQL、AccelOps
4. 修Windows平台编译链接
5. 修q35 lowmem、KVM VAPIC、ROMD这类运行问题
6. 修HLT/state-change exit
7. 修SMP CPUID
8. 补文档和验证记录
```

这样以后如果某个问题回归，比较容易定位  

比如黑屏问题就可能在：

```text
q35 lowmem
KVM VAPIC
ROMD
HLT sync
```

如果这些全在一个大提交里，就只能慢慢翻  

CPUID更建议单独提交  
因为它的行为很微妙  
而且很容易出现这种情况：

```text
4 vCPU OVMF好了
1 vCPU Linux炸了
```

或者：

```text
Linux不再卡initramfs
但是4 vCPU慢得像单核
```

这种改动和普通API迁移不是一个性质  
单独放着以后好回滚、好对比  

如果只是自己留分支，至少commit message要写清楚现象  
不要只写：

```text
fix hax
```

这种以后自己看了也想打自己  

可以写成类似：

```text
target/i386/hax: limit CPUID override to SMP leaf 1
target/i386/hax: restore SMP CPUID parallelism
hw/i386: keep q35 VGA lowmem visible with HAX
```

标题直接说修了什么现象背后的机制  
以后翻log会舒服很多  

我这次后面补到`v11.0.1_feat/HAXM`分支里的两个CPUID提交就是这种思路：

```text
28dbff6fad target/i386/hax: limit CPUID override to SMP leaf 1
1dc6af552c target/i386/hax: restore SMP CPUID parallelism
```

前一个提交解决“不要完整下发QEMU CPUID、不要破坏单vCPU Linux”的问题  
后一个提交解决“早期串行RUN可以保正确性，但进入Linux后必须恢复并行”的问题  

这两个如果揉成一个提交也能跑  
但以后看历史就会很难分清：

```text
为什么只下发leaf 1
为什么EBX低24位不用QEMU的值
为什么单vCPU直接跳过SET_CPUID
为什么一开始锁RUN，后面又放开RUN
```

拆开之后，至少回头看log时还能知道每一步是在修哪个现象  

## 结束语
这次折腾下来，感觉恢复一个被删掉的后端，比想象中麻烦很多  

因为后端代码只是最明显的一块  
真正麻烦的是它散落在QEMU各处的入口和特殊判断  

比如：

```text
q35的smm-ranges
apic_common里的kvmvapic排除
vl.c里的hax_sync_vcpus
hw_accel.h里的硬件加速器判断
scripts/meson-buildoptions.sh里的configure入口
```

这些地方漏一个，可能都不是编译时报错  
而是运行时黑屏、卡HLT、卡OVMF、卡Linux initramfs  

HAXM这个老东西最坑的还是CPUID  
共享CPUID缓冲这个设计，在SMP guest里就是很难受  
没有CPUID exit就只能做阶段性折中  

早期为了正确性锁住RUN，进入内核后为了性能放开RUN  

这办法不漂亮，但是能跑  

对于这种已经停止维护的后端来说，能跑比漂亮重要  
反正它也不是什么未来方向  
能给历史环境留条路，就算这次折腾没白费了

后面如果还要继续修，我估计最值得看的就两个方向  

一个是POSIX HAX路径  
这次基本没实测  
Darwin/NetBSD如果还有人想用，肯定不能只靠“能编译”就算完  

另一个是更完整的guest压力  
现在只是确认能启动、能进登录、Web/SSH能响应  
像dirty logging、迁移、调试、长时间多核压力这些，都还没碰  

不过说实话，这东西大概率也就维护到“还能跑一些历史场景”就差不多了  
再往现代虚拟化方案方向硬追，意义不大
