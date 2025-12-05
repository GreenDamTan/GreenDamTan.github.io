# ARM新品NAS初见，Zettlab-D4的zettos上手速览

# 前言
最近有一台家nas厂子出了个叫ZETTOS的系统，这不得摸一下？  
官网 https://zettlab.com/pages/system-page

找了一圈没有发现有SSH开关  
还好系统完成度不高，安全性的问题也不少  
很简单摸个漏洞注入拿个root然后偷个shell摸下系统

# 系统基本信息摸排
外观什么的就不说了，系统感觉是还行的，RK3588的处理器，我们的老朋友了
![20251205174848.png](img/20251205174848.png)

## kernel
不过内核是旧了点，估计是没从瑞芯微那边拿到大客户支持  
用的估计还是公开版本的内核  
```text
Linux Zettlab-D4 6.1.99 #25 SMP Tue Oct 21 15:56:22 CST 2025 aarch64 GNU/Linux
```


看看内核版本
```text
root@Zettlab-D4:/# cat /proc/version
Linux version 6.1.99 (jenkins@zhoulingfeng-ONDA-B450S-W) (aarch64-none-linux-gnu-gcc (GNU Toolchain for the A-profile Architecture 10.3-2021.07 (arm-10.29)) 10.3.1 20210621, GNU ld (GNU Toolchain for the A-profile Architecture 10.3-2021.07 (arm-10.29)) 2.36.1.20210621) #25 SMP Tue Oct 21 15:56:22 CST 2025
```

再看看内核启动参数  
```text
root@Zettlab-D4:/proc# cat cmdline
storagemedia=emmc androidboot.storagemedia=emmc androidboot.mode=normal  fuse.programmed=1 android_slotsufix=_a root=zettroot_a dump_initrd ro rootwait quiet console=ttyFIQ0 irqchip.gicv3_pseudo_nmi=0 rootfstype=squashfs rcupdate.rcu_expedited=1 rcu_nocbs=all watchdog.enable=1 androidboot.fwver=ddr-v1.18-9fa84341ce,bl31-v1.47,bl32-v1.19,uboot-a0b7608904-10/26/2025
```

## mem
内存的话给到了16G，在这个内存价格暴涨的时代弥足珍贵，大容量内存也给足了AI未来的可能性  
没有开启swap与zram
```text
root@Zettlab-D4:/# free -hm
               total        used        free      shared  buff/cache   available
Mem:            15Gi       7.3Gi       1.1Gi       2.5Gi        12Gi       8.2Gi
Swap:             0B          0B          0B
```

## emmc
内置的emmc分区如下  
其中mmcblk0p9是可读性的用户数据分区，mmcblk0p8与mmcblk0p7是系统squashfs分区  
```text
mmcblk0
     179:0    0  29.1G  0 disk
├─mmcblk0p1
│    179:1    0     4M  0 part
├─mmcblk0p2
│    179:2    0     4M  0 part
├─mmcblk0p3
│    179:3    0     4M  0 part
├─mmcblk0p4
│    179:4    0    96M  0 part
├─mmcblk0p5
│    179:5    0    96M  0 part
├─mmcblk0p6
│    179:6    0   100M  0 part
├─mmcblk0p7
│    179:7    0     1G  0 part
├─mmcblk0p8
│    179:8    0     1G  0 part
└─mmcblk0p9
     179:9    0  26.8G  0 part
mmcblk0boot0
     179:32   0     4M  1 disk
mmcblk0boot1
     179:64   0     4M  1 disk
zram0
     253:0    0     0B  0 disk
nvme0n1
     259:0    0 476.9G  0 disk
├─nvme0n1p1
│    259:1    0     1M  0 part
└─nvme0n1p2
     259:2    0 476.9G  0 part
```
整体dump出来发现有32G大小  
![20251205201234.png](img/20251205201234.png)

## cpu
处理器信息  
```text
root@Zettlab-D4:/# lscpu
Architecture:             aarch64
  CPU op-mode(s):         32-bit, 64-bit
  Byte Order:             Little Endian
CPU(s):                   8
  On-line CPU(s) list:    0-7
Vendor ID:                ARM
  Model name:             Cortex-A55
    Model:                0
    Thread(s) per core:   1
    Core(s) per socket:   4
    Socket(s):            1
    Stepping:             r2p0
    CPU(s) scaling MHz:   100%
    CPU max MHz:          1800.0000
    CPU min MHz:          408.0000
    BogoMIPS:             48.00
    Flags:                fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp
  Model name:             Cortex-A76
    Model:                0
    Thread(s) per core:   1
    Core(s) per socket:   4
    Socket(s):            1
    Stepping:             r4p0
    CPU(s) scaling MHz:   100%
    CPU max MHz:          2352.0000
    CPU min MHz:          408.0000
    BogoMIPS:             48.00
    Flags:                fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp
Caches (sum of all):
  L1d:                    384 KiB (8 instances)
  L1i:                    384 KiB (8 instances)
  L2:                     2.5 MiB (8 instances)
  L3:                     3 MiB (1 instance)
Vulnerabilities:
  Gather data sampling:   Not affected
  Itlb multihit:          Not affected
  L1tf:                   Not affected
  Mds:                    Not affected
  Meltdown:               Not affected
  Mmio stale data:        Not affected
  Reg file data sampling: Not affected
  Retbleed:               Not affected
  Spec rstack overflow:   Not affected
  Spec store bypass:      Mitigation; Speculative Store Bypass disabled via prctl
  Spectre v1:             Mitigation; __user pointer sanitization
  Spectre v2:             Mitigation; CSV2, BHB
  Srbds:                  Not affected
  Tsx async abort:        Not affected
```

## sources.list
机器使用了Debian12系统，然后里面带了一个抽象的本地源  
估计后续是不打算开放系统ssh的，也不打算使用apt安装包或者是安全更新  
```text
root@Zettlab-D4:/etc/apt# tail sources.list
deb http://192.168.31.42:8080/debian bookworm main non-free non-free-firmware
root@Zettlab-D4:/etc/apt# tail sources.list.d/*
==> sources.list.d/docker.list <==
deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian   bookworm stable

==> sources.list.d/groonga.sources <==
Types: deb deb-src
URIs: https://packages.groonga.org/debian/
Suites: bookworm
Components: main
Signed-By: /usr/share/keyrings/groonga-archive-keyring.asc

==> sources.list.d/pgdg.list <==
deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main
```

## ls -la /dev/*
没有binder设备，跑不了安卓容器  
```text
root@Zettlab-D4-2dc8:/# ls -la /dev/*
crw-r--r--  1 root root     10, 235 Dec  5 16:51 /dev/autofs
brw-rw----  1 root disk    252,   0 Dec  5 16:51 /dev/bcache0
brw-rw----  1 root disk    252, 128 Dec  5 16:51 /dev/bcache1
brw-rw----  1 root disk    252, 256 Dec  5 16:52 /dev/bcache2
crw-rw----  1 root disk     10, 234 Dec  5 16:51 /dev/btrfs-control
crw-------  1 root root      5,   1 Dec  5 16:52 /dev/console
crw-------  1 root root     10, 124 Dec  5 16:51 /dev/cpu_dma_latency
crw-rw-rw-  1 root root     10, 125 Dec  5 16:51 /dev/crypto
crw-------  1 root root    240,   0 Dec  5 16:51 /dev/drm_dp_aux0
lrwxrwxrwx  1 root root          13 Dec  5 16:51 /dev/fd -> /proc/self/fd
crw-rw-rw-  1 root root      1,   7 Dec  5 16:51 /dev/full
crw-rw-rw-  1 root root     10, 229 Dec  5 16:51 /dev/fuse
crw-------  1 root root    254,   0 Dec  5 16:51 /dev/gpiochip0
crw-------  1 root root    254,   1 Dec  5 16:51 /dev/gpiochip1
crw-------  1 root root    254,   2 Dec  5 16:51 /dev/gpiochip2
crw-------  1 root root    254,   3 Dec  5 16:51 /dev/gpiochip3
crw-------  1 root root    254,   4 Dec  5 16:51 /dev/gpiochip4
crw-------  1 root root    254,   5 Dec  5 16:51 /dev/gpiochip5
crw-------  1 root root     10, 183 Dec  5 16:51 /dev/hwrng
crw-------  1 root root     89,   0 Dec  5 16:51 /dev/i2c-0
crw-------  1 root root     89,   1 Dec  5 16:51 /dev/i2c-1
crw-------  1 root root     89,  10 Dec  5 16:51 /dev/i2c-10
crw-------  1 root root     89,   2 Dec  5 16:51 /dev/i2c-2
crw-------  1 root root     89,   6 Dec  5 16:51 /dev/i2c-6
crw-------  1 root root     89,   9 Dec  5 16:51 /dev/i2c-9
crw-------  1 root root    245,   0 Dec  5 16:51 /dev/iio:device0
lrwxrwxrwx  1 root root          12 Dec  5 16:51 /dev/initctl -> /run/initctl
crw-r--r--  1 root root      1,  11 Dec  5 16:51 /dev/kmsg
crw-rw----  1 root kvm      10, 232 Dec  5 19:59 /dev/kvm
lrwxrwxrwx  1 root root          28 Dec  5 16:51 /dev/log -> /run/systemd/journal/dev-log
brw-rw----  1 root disk      7,   0 Dec  5 16:51 /dev/loop0
brw-rw----  1 root disk      7,   1 Dec  5 16:51 /dev/loop1
brw-rw----  1 root disk      7,   2 Dec  5 16:51 /dev/loop2
brw-rw----  1 root disk      7,   3 Dec  5 16:51 /dev/loop3
brw-rw----  1 root disk      7,   4 Dec  5 16:51 /dev/loop4
brw-rw----  1 root disk      7,   5 Dec  5 16:51 /dev/loop5
brw-rw----  1 root disk      7,   6 Dec  5 16:51 /dev/loop6
brw-rw----  1 root disk      7,   7 Dec  5 16:51 /dev/loop7
crw-rw----  1 root disk     10, 237 Dec  5 16:51 /dev/loop-control
crw-rw-rw-  1 root root     10, 122 Dec  5 16:51 /dev/mali0
brw-rw----  1 root disk      9,   0 Dec  5 16:51 /dev/md0
brw-rw----  1 root disk      9,   1 Dec  5 16:51 /dev/md1
brw-rw----  1 root disk      9,   2 Dec  5 16:52 /dev/md2
crw-r-----  1 root kmem      1,   1 Dec  5 16:51 /dev/mem
brw-rw----  1 root disk    179,   0 Dec  5 16:51 /dev/mmcblk0
brw-rw----  1 root disk    179,  32 Dec  5 16:51 /dev/mmcblk0boot0
brw-rw----  1 root disk    179,  64 Dec  5 16:51 /dev/mmcblk0boot1
brw-rw----  1 root disk    179,   1 Dec  5 16:51 /dev/mmcblk0p1
brw-rw----  1 root disk    179,   2 Dec  5 16:51 /dev/mmcblk0p2
brw-rw----  1 root disk    179,   3 Dec  5 16:51 /dev/mmcblk0p3
brw-rw----  1 root disk    179,   4 Dec  5 16:51 /dev/mmcblk0p4
brw-rw----  1 root disk    179,   5 Dec  5 16:51 /dev/mmcblk0p5
brw-rw----  1 root disk    179,   6 Dec  5 16:51 /dev/mmcblk0p6
brw-rw----  1 root disk    179,   7 Dec  5 16:51 /dev/mmcblk0p7
brw-rw----  1 root disk    179,   8 Dec  5 16:51 /dev/mmcblk0p8
brw-rw----  1 root disk    179,   9 Dec  5 16:51 /dev/mmcblk0p9
crw-------  1 root root    234,   0 Dec  5 16:51 /dev/mmcblk0rpmb
crw-------  1 root root    241,   0 Dec  5 16:51 /dev/mpp_service
crw-------  1 root root    238,   0 Dec  5 16:51 /dev/ng0n1
crw-rw-rw-  1 root root      1,   3 Dec  5 16:51 /dev/null
crw-------  1 root root    239,   0 Dec  5 16:51 /dev/nvme0
brw-rw----  1 root disk    259,   0 Dec  5 16:51 /dev/nvme0n1
brw-rw----  1 root disk    259,   1 Dec  5 16:51 /dev/nvme0n1p1
brw-rw----  1 root disk    259,   2 Dec  5 16:51 /dev/nvme0n1p2
crw-r-----  1 root kmem      1,   4 Dec  5 16:51 /dev/port
crw-rw-rw-  1 root tty       5,   2 Dec  5 20:06 /dev/ptmx
crw-------  1 root root    246,   0 Dec  5 16:51 /dev/ptp0
brw-rw----  1 root disk      1,   0 Dec  5 16:51 /dev/ram0
crw-rw-rw-  1 root root      1,   8 Dec  5 16:51 /dev/random
crw-rw-r--  1 root netdev   10, 242 Dec  5 16:51 /dev/rfkill
crw-------  1 root root     10, 123 Dec  5 16:51 /dev/rga
lrwxrwxrwx  1 root root           4 Dec  5 16:51 /dev/rtc -> rtc0
crw-------  1 root root    250,   0 Dec  5 16:51 /dev/rtc0
brw-rw----  1 root disk      8,   0 Dec  5 16:51 /dev/sda
brw-rw----  1 root disk      8,  16 Dec  5 16:51 /dev/sdb
brw-rw----  1 root disk      8,  32 Dec  5 16:51 /dev/sdc
brw-rw----  1 root disk      8,  33 Dec  5 16:51 /dev/sdc1
brw-rw----  1 root disk      8,  34 Dec  5 16:51 /dev/sdc2
brw-rw----  1 root disk      8,  35 Dec  5 16:52 /dev/sdc3
brw-rw----  1 root disk      8,  48 Dec  5 16:51 /dev/sdd
brw-rw----  1 root disk      8,  49 Dec  5 16:51 /dev/sdd1
brw-rw----  1 root disk      8,  50 Dec  5 16:51 /dev/sdd2
brw-rw----  1 root disk      8,  51 Dec  5 16:52 /dev/sdd3
lrwxrwxrwx  1 root root          15 Dec  5 16:51 /dev/stderr -> /proc/self/fd/2
lrwxrwxrwx  1 root root          15 Dec  5 16:51 /dev/stdin -> /proc/self/fd/0
lrwxrwxrwx  1 root root          15 Dec  5 16:51 /dev/stdout -> /proc/self/fd/1
crw-------  1 root root     10, 127 Dec  5 16:51 /dev/sw_sync
crw-------  1 root root    244,   0 Dec  5 16:51 /dev/tee0
crw-------  1 root root    244,  16 Dec  5 16:51 /dev/teepriv0
crw-rw-rw-  1 root tty       5,   0 Dec  5 16:51 /dev/tty
crw--w----  1 root tty       4,   0 Dec  5 16:51 /dev/tty0
crw--w----  1 root tty       4,   1 Dec  5 16:52 /dev/tty1
crw--w----  1 root tty       4,  10 Dec  5 16:51 /dev/tty10
crw--w----  1 root tty       4,  11 Dec  5 16:51 /dev/tty11
crw--w----  1 root tty       4,  12 Dec  5 16:51 /dev/tty12
crw--w----  1 root tty       4,  13 Dec  5 16:51 /dev/tty13
crw--w----  1 root tty       4,  14 Dec  5 16:51 /dev/tty14
crw--w----  1 root tty       4,  15 Dec  5 16:51 /dev/tty15
crw--w----  1 root tty       4,  16 Dec  5 16:51 /dev/tty16
crw--w----  1 root tty       4,  17 Dec  5 16:51 /dev/tty17
crw--w----  1 root tty       4,  18 Dec  5 16:51 /dev/tty18
crw--w----  1 root tty       4,  19 Dec  5 16:51 /dev/tty19
crw--w----  1 root tty       4,   2 Dec  5 16:51 /dev/tty2
crw--w----  1 root tty       4,  20 Dec  5 16:51 /dev/tty20
crw--w----  1 root tty       4,  21 Dec  5 16:51 /dev/tty21
crw--w----  1 root tty       4,  22 Dec  5 16:51 /dev/tty22
crw--w----  1 root tty       4,  23 Dec  5 16:51 /dev/tty23
crw--w----  1 root tty       4,  24 Dec  5 16:51 /dev/tty24
crw--w----  1 root tty       4,  25 Dec  5 16:51 /dev/tty25
crw--w----  1 root tty       4,  26 Dec  5 16:51 /dev/tty26
crw--w----  1 root tty       4,  27 Dec  5 16:51 /dev/tty27
crw--w----  1 root tty       4,  28 Dec  5 16:51 /dev/tty28
crw--w----  1 root tty       4,  29 Dec  5 16:51 /dev/tty29
crw--w----  1 root tty       4,   3 Dec  5 16:51 /dev/tty3
crw--w----  1 root tty       4,  30 Dec  5 16:51 /dev/tty30
crw--w----  1 root tty       4,  31 Dec  5 16:51 /dev/tty31
crw--w----  1 root tty       4,  32 Dec  5 16:51 /dev/tty32
crw--w----  1 root tty       4,  33 Dec  5 16:51 /dev/tty33
crw--w----  1 root tty       4,  34 Dec  5 16:51 /dev/tty34
crw--w----  1 root tty       4,  35 Dec  5 16:51 /dev/tty35
crw--w----  1 root tty       4,  36 Dec  5 16:51 /dev/tty36
crw--w----  1 root tty       4,  37 Dec  5 16:51 /dev/tty37
crw--w----  1 root tty       4,  38 Dec  5 16:51 /dev/tty38
crw--w----  1 root tty       4,  39 Dec  5 16:51 /dev/tty39
crw--w----  1 root tty       4,   4 Dec  5 16:51 /dev/tty4
crw--w----  1 root tty       4,  40 Dec  5 16:51 /dev/tty40
crw--w----  1 root tty       4,  41 Dec  5 16:51 /dev/tty41
crw--w----  1 root tty       4,  42 Dec  5 16:51 /dev/tty42
crw--w----  1 root tty       4,  43 Dec  5 16:51 /dev/tty43
crw--w----  1 root tty       4,  44 Dec  5 16:51 /dev/tty44
crw--w----  1 root tty       4,  45 Dec  5 16:51 /dev/tty45
crw--w----  1 root tty       4,  46 Dec  5 16:51 /dev/tty46
crw--w----  1 root tty       4,  47 Dec  5 16:51 /dev/tty47
crw--w----  1 root tty       4,  48 Dec  5 16:51 /dev/tty48
crw--w----  1 root tty       4,  49 Dec  5 16:51 /dev/tty49
crw--w----  1 root tty       4,   5 Dec  5 16:51 /dev/tty5
crw--w----  1 root tty       4,  50 Dec  5 16:51 /dev/tty50
crw--w----  1 root tty       4,  51 Dec  5 16:51 /dev/tty51
crw--w----  1 root tty       4,  52 Dec  5 16:51 /dev/tty52
crw--w----  1 root tty       4,  53 Dec  5 16:51 /dev/tty53
crw--w----  1 root tty       4,  54 Dec  5 16:51 /dev/tty54
crw--w----  1 root tty       4,  55 Dec  5 16:51 /dev/tty55
crw--w----  1 root tty       4,  56 Dec  5 16:51 /dev/tty56
crw--w----  1 root tty       4,  57 Dec  5 16:51 /dev/tty57
crw--w----  1 root tty       4,  58 Dec  5 16:51 /dev/tty58
crw--w----  1 root tty       4,  59 Dec  5 16:51 /dev/tty59
crw--w----  1 root tty       4,   6 Dec  5 16:51 /dev/tty6
crw--w----  1 root tty       4,  60 Dec  5 16:51 /dev/tty60
crw--w----  1 root tty       4,  61 Dec  5 16:51 /dev/tty61
crw--w----  1 root tty       4,  62 Dec  5 16:51 /dev/tty62
crw--w----  1 root tty       4,  63 Dec  5 16:51 /dev/tty63
crw--w----  1 root tty       4,   7 Dec  5 16:51 /dev/tty7
crw--w----  1 root tty       4,   8 Dec  5 16:51 /dev/tty8
crw--w----  1 root tty       4,   9 Dec  5 16:51 /dev/tty9
crw-rw----  1 root dialout 253,   0 Dec  5 16:52 /dev/ttyFIQ0
crw-rw----  1 root dialout   4,  71 Dec  5 16:51 /dev/ttyS7
crw-------  1 root root     10, 239 Dec  5 16:51 /dev/uhid
crw-------  1 root root     10, 223 Dec  5 16:51 /dev/uinput
crw-rw-rw-  1 root root      1,   9 Dec  5 16:51 /dev/urandom
crw-------  1 root root    236,   0 Dec  5 16:51 /dev/usbmon0
crw-------  1 root root    236,   1 Dec  5 16:51 /dev/usbmon1
crw-------  1 root root    236,   2 Dec  5 16:51 /dev/usbmon2
crw-------  1 root root    236,   3 Dec  5 16:51 /dev/usbmon3
crw-------  1 root root    236,   4 Dec  5 16:51 /dev/usbmon4
crw-------  1 root root    236,   5 Dec  5 16:51 /dev/usbmon5
crw-------  1 root root    236,   6 Dec  5 16:51 /dev/usbmon6
crw-------  1 root root    236,   7 Dec  5 16:51 /dev/usbmon7
crw-------  1 root root    236,   8 Dec  5 16:51 /dev/usbmon8
crw-rw----  1 root tty       7,   0 Dec  5 16:51 /dev/vcs
crw-rw----  1 root tty       7,   1 Dec  5 16:51 /dev/vcs1
crw-rw----  1 root tty       7,   2 Dec  5 16:51 /dev/vcs2
crw-rw----  1 root tty       7,   3 Dec  5 16:51 /dev/vcs3
crw-rw----  1 root tty       7,   4 Dec  5 16:51 /dev/vcs4
crw-rw----  1 root tty       7,   5 Dec  5 16:51 /dev/vcs5
crw-rw----  1 root tty       7,   6 Dec  5 16:51 /dev/vcs6
crw-rw----  1 root tty       7, 128 Dec  5 16:51 /dev/vcsa
crw-rw----  1 root tty       7, 129 Dec  5 16:51 /dev/vcsa1
crw-rw----  1 root tty       7, 130 Dec  5 16:51 /dev/vcsa2
crw-rw----  1 root tty       7, 131 Dec  5 16:51 /dev/vcsa3
crw-rw----  1 root tty       7, 132 Dec  5 16:51 /dev/vcsa4
crw-rw----  1 root tty       7, 133 Dec  5 16:51 /dev/vcsa5
crw-rw----  1 root tty       7, 134 Dec  5 16:51 /dev/vcsa6
crw-rw----  1 root tty       7,  64 Dec  5 16:51 /dev/vcsu
crw-rw----  1 root tty       7,  65 Dec  5 16:51 /dev/vcsu1
crw-rw----  1 root tty       7,  66 Dec  5 16:51 /dev/vcsu2
crw-rw----  1 root tty       7,  67 Dec  5 16:51 /dev/vcsu3
crw-rw----  1 root tty       7,  68 Dec  5 16:51 /dev/vcsu4
crw-rw----  1 root tty       7,  69 Dec  5 16:51 /dev/vcsu5
crw-rw----  1 root tty       7,  70 Dec  5 16:51 /dev/vcsu6
crw-------  1 root root     10, 121 Dec  5 16:51 /dev/vendor_storage
crw-------  1 root root     10, 130 Dec  5 16:51 /dev/watchdog
crw-------  1 root root    243,   0 Dec  5 16:51 /dev/watchdog0
crw-rw-rw-  1 root root      1,   5 Dec  5 16:51 /dev/zero
crw-------  1 root root     10, 126 Dec  5 16:51 /dev/zettos
brw-rw----  1 root disk    253,   0 Dec  5 16:51 /dev/zram0

/dev/bcache:
total 0
drwxr-xr-x  3 root root   60 Dec  5 16:51 .
drwxr-xr-x 17 root root 4240 Dec  5 16:52 ..
drwxr-xr-x  2 root root  100 Dec  5 16:52 by-uuid

/dev/block:
total 0
drwxr-xr-x  2 root root  860 Dec  5 16:52 .
drwxr-xr-x 17 root root 4240 Dec  5 16:52 ..
lrwxrwxrwx  1 root root    7 Dec  5 16:51 1:0 -> ../ram0
lrwxrwxrwx  1 root root   10 Dec  5 16:51 179:0 -> ../mmcblk0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:1 -> ../mmcblk0p1
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:2 -> ../mmcblk0p2
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:3 -> ../mmcblk0p3
lrwxrwxrwx  1 root root   15 Dec  5 16:51 179:32 -> ../mmcblk0boot0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:4 -> ../mmcblk0p4
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:5 -> ../mmcblk0p5
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:6 -> ../mmcblk0p6
lrwxrwxrwx  1 root root   15 Dec  5 16:51 179:64 -> ../mmcblk0boot1
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:7 -> ../mmcblk0p7
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:8 -> ../mmcblk0p8
lrwxrwxrwx  1 root root   12 Dec  5 16:51 179:9 -> ../mmcblk0p9
lrwxrwxrwx  1 root root   10 Dec  5 16:51 252:0 -> ../bcache0
lrwxrwxrwx  1 root root   10 Dec  5 16:51 252:128 -> ../bcache1
lrwxrwxrwx  1 root root   10 Dec  5 16:52 252:256 -> ../bcache2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 253:0 -> ../zram0
lrwxrwxrwx  1 root root   10 Dec  5 16:51 259:0 -> ../nvme0n1
lrwxrwxrwx  1 root root   12 Dec  5 16:51 259:1 -> ../nvme0n1p1
lrwxrwxrwx  1 root root   12 Dec  5 16:51 259:2 -> ../nvme0n1p2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:0 -> ../loop0
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:1 -> ../loop1
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:2 -> ../loop2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:3 -> ../loop3
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:4 -> ../loop4
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:5 -> ../loop5
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:6 -> ../loop6
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:7 -> ../loop7
lrwxrwxrwx  1 root root    6 Dec  5 16:51 8:0 -> ../sda
lrwxrwxrwx  1 root root    6 Dec  5 16:51 8:16 -> ../sdb
lrwxrwxrwx  1 root root    6 Dec  5 16:51 8:32 -> ../sdc
lrwxrwxrwx  1 root root    7 Dec  5 16:51 8:33 -> ../sdc1
lrwxrwxrwx  1 root root    7 Dec  5 16:51 8:34 -> ../sdc2
lrwxrwxrwx  1 root root    7 Dec  5 16:52 8:35 -> ../sdc3
lrwxrwxrwx  1 root root    6 Dec  5 16:51 8:48 -> ../sdd
lrwxrwxrwx  1 root root    7 Dec  5 16:51 8:49 -> ../sdd1
lrwxrwxrwx  1 root root    7 Dec  5 16:51 8:50 -> ../sdd2
lrwxrwxrwx  1 root root    7 Dec  5 16:52 8:51 -> ../sdd3
lrwxrwxrwx  1 root root    6 Dec  5 16:51 9:0 -> ../md0
lrwxrwxrwx  1 root root    6 Dec  5 16:51 9:1 -> ../md1
lrwxrwxrwx  1 root root    6 Dec  5 16:52 9:2 -> ../md2

/dev/bsg:
total 0
drwxr-xr-x  2 root root    120 Dec  5 16:51 .
drwxr-xr-x 17 root root   4240 Dec  5 16:52 ..
crw-------  1 root root 242, 0 Dec  5 16:51 2:0:0:0
crw-------  1 root root 242, 1 Dec  5 16:51 2:0:0:1
crw-------  1 root root 242, 2 Dec  5 16:51 5:0:0:0
crw-------  1 root root 242, 3 Dec  5 16:51 6:0:0:0

/dev/bus:
total 0
drwxr-xr-x  3 root root   60 Jan  1  1970 .
drwxr-xr-x 17 root root 4240 Dec  5 16:52 ..
drwxr-xr-x 10 root root  200 Dec  5 16:51 usb

/dev/char:
total 0
drwxr-xr-x  2 root root 3600 Dec  5 19:59 .
drwxr-xr-x 17 root root 4240 Dec  5 16:52 ..
lrwxrwxrwx  1 root root   17 Dec  5 16:51 10:121 -> ../vendor_storage
lrwxrwxrwx  1 root root    8 Dec  5 16:51 10:122 -> ../mali0
lrwxrwxrwx  1 root root    6 Dec  5 16:51 10:123 -> ../rga
lrwxrwxrwx  1 root root   18 Dec  5 16:51 10:124 -> ../cpu_dma_latency
lrwxrwxrwx  1 root root    9 Dec  5 16:51 10:125 -> ../crypto
lrwxrwxrwx  1 root root    9 Dec  5 16:51 10:126 -> ../zettos
lrwxrwxrwx  1 root root   10 Dec  5 16:51 10:127 -> ../sw_sync
lrwxrwxrwx  1 root root   11 Dec  5 16:51 10:130 -> ../watchdog
lrwxrwxrwx  1 root root    8 Dec  5 16:51 10:183 -> ../hwrng
lrwxrwxrwx  1 root root   10 Dec  5 16:51 10:200 -> ../net/tun
lrwxrwxrwx  1 root root    9 Dec  5 16:51 10:223 -> ../uinput
lrwxrwxrwx  1 root root    7 Dec  5 16:51 10:229 -> ../fuse
lrwxrwxrwx  1 root root    6 Dec  5 19:59 10:232 -> ../kvm
lrwxrwxrwx  1 root root   16 Dec  5 16:51 10:234 -> ../btrfs-control
lrwxrwxrwx  1 root root    9 Dec  5 16:51 10:235 -> ../autofs
lrwxrwxrwx  1 root root   17 Dec  5 16:51 10:236 -> ../mapper/control
lrwxrwxrwx  1 root root   15 Dec  5 16:51 10:237 -> ../loop-control
lrwxrwxrwx  1 root root    7 Dec  5 16:51 10:239 -> ../uhid
lrwxrwxrwx  1 root root    9 Dec  5 16:51 10:242 -> ../rfkill
lrwxrwxrwx  1 root root    6 Dec  5 16:51 1:1 -> ../mem
lrwxrwxrwx  1 root root    7 Dec  5 16:51 1:11 -> ../kmsg
lrwxrwxrwx  1 root root   10 Dec  5 16:51 116:1 -> ../snd/seq
lrwxrwxrwx  1 root root   15 Dec  5 16:51 116:2 -> ../snd/pcmC0D0p
lrwxrwxrwx  1 root root   16 Dec  5 16:51 116:3 -> ../snd/controlC0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 116:33 -> ../snd/timer
lrwxrwxrwx  1 root root    7 Dec  5 16:51 1:3 -> ../null
lrwxrwxrwx  1 root root   15 Dec  5 16:51 13:64 -> ../input/event0
lrwxrwxrwx  1 root root   15 Dec  5 16:51 13:65 -> ../input/event1
lrwxrwxrwx  1 root root   15 Dec  5 16:51 13:66 -> ../input/event2
lrwxrwxrwx  1 root root   15 Dec  5 16:51 13:67 -> ../input/event3
lrwxrwxrwx  1 root root    7 Dec  5 16:51 1:4 -> ../port
lrwxrwxrwx  1 root root    7 Dec  5 16:51 1:5 -> ../zero
lrwxrwxrwx  1 root root    7 Dec  5 16:51 1:7 -> ../full
lrwxrwxrwx  1 root root    9 Dec  5 16:51 1:8 -> ../random
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:0 -> ../bus/usb/001/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:128 -> ../bus/usb/002/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:129 -> ../bus/usb/002/002
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:256 -> ../bus/usb/003/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:384 -> ../bus/usb/004/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:512 -> ../bus/usb/005/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:513 -> ../bus/usb/005/002
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:640 -> ../bus/usb/006/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:768 -> ../bus/usb/007/001
lrwxrwxrwx  1 root root   18 Dec  5 16:51 189:896 -> ../bus/usb/008/001
lrwxrwxrwx  1 root root   10 Dec  5 16:51 1:9 -> ../urandom
lrwxrwxrwx  1 root root   12 Dec  5 16:51 226:0 -> ../dri/card0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 226:1 -> ../dri/card1
lrwxrwxrwx  1 root root   17 Dec  5 16:51 226:128 -> ../dri/renderD128
lrwxrwxrwx  1 root root   17 Dec  5 16:51 226:129 -> ../dri/renderD129
lrwxrwxrwx  1 root root   14 Dec  5 16:51 234:0 -> ../mmcblk0rpmb
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:0 -> ../usbmon0
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:1 -> ../usbmon1
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:2 -> ../usbmon2
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:3 -> ../usbmon3
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:4 -> ../usbmon4
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:5 -> ../usbmon5
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:6 -> ../usbmon6
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:7 -> ../usbmon7
lrwxrwxrwx  1 root root   10 Dec  5 16:51 236:8 -> ../usbmon8
lrwxrwxrwx  1 root root    8 Dec  5 16:51 238:0 -> ../ng0n1
lrwxrwxrwx  1 root root    8 Dec  5 16:51 239:0 -> ../nvme0
lrwxrwxrwx  1 root root   14 Dec  5 16:51 240:0 -> ../drm_dp_aux0
lrwxrwxrwx  1 root root   14 Dec  5 16:51 241:0 -> ../mpp_service
lrwxrwxrwx  1 root root   14 Dec  5 16:51 242:0 -> ../bsg/2:0:0:0
lrwxrwxrwx  1 root root   14 Dec  5 16:51 242:1 -> ../bsg/2:0:0:1
lrwxrwxrwx  1 root root   14 Dec  5 16:51 242:2 -> ../bsg/5:0:0:0
lrwxrwxrwx  1 root root   14 Dec  5 16:51 242:3 -> ../bsg/6:0:0:0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 243:0 -> ../watchdog0
lrwxrwxrwx  1 root root    7 Dec  5 16:51 244:0 -> ../tee0
lrwxrwxrwx  1 root root   11 Dec  5 16:51 244:16 -> ../teepriv0
lrwxrwxrwx  1 root root   14 Dec  5 16:51 245:0 -> ../iio:device0
lrwxrwxrwx  1 root root    7 Dec  5 16:51 246:0 -> ../ptp0
lrwxrwxrwx  1 root root    7 Dec  5 16:51 250:0 -> ../rtc0
lrwxrwxrwx  1 root root   18 Dec  5 16:51 251:0 -> ../dma_heap/system
lrwxrwxrwx  1 root root   27 Dec  5 16:51 251:1 -> ../dma_heap/system-uncached
lrwxrwxrwx  1 root root   15 Dec  5 16:51 251:2 -> ../dma_heap/cma
lrwxrwxrwx  1 root root   10 Dec  5 16:51 253:0 -> ../ttyFIQ0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 254:0 -> ../gpiochip0
lrwxrwxrwx  1 root root   12 Dec  5 16:51 254:1 -> ../gpiochip1
lrwxrwxrwx  1 root root   12 Dec  5 16:51 254:2 -> ../gpiochip2
lrwxrwxrwx  1 root root   12 Dec  5 16:51 254:3 -> ../gpiochip3
lrwxrwxrwx  1 root root   12 Dec  5 16:51 254:4 -> ../gpiochip4
lrwxrwxrwx  1 root root   12 Dec  5 16:51 254:5 -> ../gpiochip5
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:0 -> ../tty0
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:1 -> ../tty1
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:10 -> ../tty10
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:11 -> ../tty11
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:12 -> ../tty12
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:13 -> ../tty13
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:14 -> ../tty14
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:15 -> ../tty15
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:16 -> ../tty16
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:17 -> ../tty17
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:18 -> ../tty18
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:19 -> ../tty19
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:2 -> ../tty2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:20 -> ../tty20
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:21 -> ../tty21
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:22 -> ../tty22
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:23 -> ../tty23
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:24 -> ../tty24
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:25 -> ../tty25
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:26 -> ../tty26
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:27 -> ../tty27
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:28 -> ../tty28
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:29 -> ../tty29
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:3 -> ../tty3
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:30 -> ../tty30
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:31 -> ../tty31
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:32 -> ../tty32
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:33 -> ../tty33
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:34 -> ../tty34
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:35 -> ../tty35
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:36 -> ../tty36
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:37 -> ../tty37
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:38 -> ../tty38
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:39 -> ../tty39
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:4 -> ../tty4
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:40 -> ../tty40
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:41 -> ../tty41
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:42 -> ../tty42
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:43 -> ../tty43
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:44 -> ../tty44
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:45 -> ../tty45
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:46 -> ../tty46
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:47 -> ../tty47
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:48 -> ../tty48
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:49 -> ../tty49
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:5 -> ../tty5
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:50 -> ../tty50
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:51 -> ../tty51
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:52 -> ../tty52
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:53 -> ../tty53
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:54 -> ../tty54
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:55 -> ../tty55
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:56 -> ../tty56
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:57 -> ../tty57
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:58 -> ../tty58
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:59 -> ../tty59
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:6 -> ../tty6
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:60 -> ../tty60
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:61 -> ../tty61
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:62 -> ../tty62
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:63 -> ../tty63
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:7 -> ../tty7
lrwxrwxrwx  1 root root    8 Dec  5 16:51 4:71 -> ../ttyS7
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:8 -> ../tty8
lrwxrwxrwx  1 root root    7 Dec  5 16:51 4:9 -> ../tty9
lrwxrwxrwx  1 root root    6 Dec  5 16:51 5:0 -> ../tty
lrwxrwxrwx  1 root root   10 Dec  5 16:51 5:1 -> ../console
lrwxrwxrwx  1 root root    7 Dec  5 16:51 5:2 -> ../ptmx
lrwxrwxrwx  1 root root    6 Dec  5 16:51 7:0 -> ../vcs
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:1 -> ../vcs1
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:128 -> ../vcsa
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:129 -> ../vcsa1
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:130 -> ../vcsa2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:131 -> ../vcsa3
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:132 -> ../vcsa4
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:133 -> ../vcsa5
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:134 -> ../vcsa6
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:2 -> ../vcs2
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:3 -> ../vcs3
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:4 -> ../vcs4
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:5 -> ../vcs5
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:6 -> ../vcs6
lrwxrwxrwx  1 root root    7 Dec  5 16:51 7:64 -> ../vcsu
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:65 -> ../vcsu1
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:66 -> ../vcsu2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:67 -> ../vcsu3
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:68 -> ../vcsu4
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:69 -> ../vcsu5
lrwxrwxrwx  1 root root    8 Dec  5 16:51 7:70 -> ../vcsu6
lrwxrwxrwx  1 root root    8 Dec  5 16:51 89:0 -> ../i2c-0
lrwxrwxrwx  1 root root    8 Dec  5 16:51 89:1 -> ../i2c-1
lrwxrwxrwx  1 root root    9 Dec  5 16:51 89:10 -> ../i2c-10
lrwxrwxrwx  1 root root    8 Dec  5 16:51 89:2 -> ../i2c-2
lrwxrwxrwx  1 root root    8 Dec  5 16:51 89:6 -> ../i2c-6
lrwxrwxrwx  1 root root    8 Dec  5 16:51 89:9 -> ../i2c-9

/dev/disk:
total 0
drwxr-xr-x 13 root root  260 Dec  5 16:51 .
drwxr-xr-x 17 root root 4240 Dec  5 16:52 ..
drwxr-xr--  2 root root  100 Dec  5 16:52 by-bcache
drwxr-xr--  2 root root   40 Dec  5 16:51 by-cache
drwxr-xr-x  2 root root  420 Dec  5 16:52 by-diskseq
drwxr-xr-x  2 root root  900 Dec  5 16:52 by-id
drwxr-xr-x  2 root root  100 Dec  5 16:52 by-label
drwxr-xr-x  2 root root  220 Dec  5 16:51 by-partlabel
drwxr-xr-x  2 root root  380 Dec  5 16:52 by-partuuid
drwxr-xr-x  2 root root  700 Dec  5 16:52 by-path
drwxr-xr--  2 root root  100 Dec  5 16:51 by-place
drwxr-xr--  2 root root  100 Dec  5 16:52 by-pool
drwxr-xr-x  2 root root  200 Dec  5 16:52 by-uuid

/dev/dma_heap:
total 0
drwxr-xr-x  2 root root    100 Jan  1  1970 .
drwxr-xr-x 17 root root   4240 Dec  5 16:52 ..
crw-------  1 root root 251, 2 Dec  5 16:51 cma
crw-------  1 root root 251, 0 Dec  5 16:51 system
crw-------  1 root root 251, 1 Dec  5 16:51 system-uncached

/dev/dri:
total 0
drwxr-xr-x  3 root root        140 Dec  5 16:51 .
drwxr-xr-x 17 root root       4240 Dec  5 16:52 ..
drwxr-xr-x  2 root root        120 Dec  5 16:51 by-path
crw-rw----  1 root video  226,   0 Dec  5 16:51 card0
crw-rw----  1 root video  226,   1 Dec  5 16:51 card1
crw-rw----  1 root render 226, 128 Dec  5 16:51 renderD128
crw-rw----  1 root render 226, 129 Dec  5 16:51 renderD129

/dev/input:
total 0
drwxr-xr-x  3 root root     140 Dec  5 16:51 .
drwxr-xr-x 17 root root    4240 Dec  5 16:52 ..
drwxr-xr-x  2 root root     120 Dec  5 16:51 by-path
crw-rw----  1 root input 13, 64 Dec  5 16:51 event0
crw-rw----  1 root input 13, 65 Dec  5 16:51 event1
crw-rw----  1 root input 13, 66 Dec  5 16:51 event2
crw-rw----  1 root input 13, 67 Dec  5 16:51 event3

/dev/mapper:
total 0
drwxr-xr-x  2 root root      60 Dec  5 16:51 .
drwxr-xr-x 17 root root    4240 Dec  5 16:52 ..
crw-------  1 root root 10, 236 Dec  5 16:51 control

/dev/mqueue:
total 0
drwxrwxrwt  2 root root  100 Dec  5 16:51 .
drwxr-xr-x 17 root root 4240 Dec  5 16:52 ..
-rw-r--r--  1 root root   80 Dec  5 16:52 app_sys_queue
-rw-r--r--  1 root root   80 Dec  5 16:52 lcd_queue
-rw-r--r--  1 root root   80 Dec  5 19:34 sys_app_queue

/dev/net:
total 0
drwxr-xr-x  2 root root      60 Jan  1  1970 .
drwxr-xr-x 17 root root    4240 Dec  5 16:52 ..
crw-rw-rw-  1 root root 10, 200 Dec  5 16:51 tun

/dev/pts:
total 0
drwxr-xr-x  2 root root      0 Dec  5 16:51 .
drwxr-xr-x 17 root root   4240 Dec  5 16:52 ..
crw--w----  1 root tty  136, 0 Dec  5 20:06 0
crw--w----  1 root tty  136, 1 Dec  5 19:59 1
c---------  1 root root   5, 2 Dec  5 16:51 ptmx

/dev/shm:
total 1052
drwxrwxrwt  2 root     root          80 Dec  5 16:52 .
drwxr-xr-x 17 root     root        4240 Dec  5 16:52 ..
-rw-------  1 postgres postgres   26976 Dec  5 16:52 PostgreSQL.114513890
-rw-------  1 postgres postgres 1048576 Dec  5 19:54 PostgreSQL.3529159332

/dev/snd:
total 0
drwxr-xr-x  3 root root      140 Dec  5 16:51 .
drwxr-xr-x 17 root root     4240 Dec  5 16:52 ..
drwxr-xr-x  2 root root       60 Dec  5 16:51 by-path
crw-rw----  1 root audio 116,  3 Dec  5 16:51 controlC0
crw-rw----  1 root audio 116,  2 Dec  5 16:51 pcmC0D0p
crw-rw----  1 root audio 116,  1 Dec  5 16:51 seq
crw-rw----  1 root audio 116, 33 Dec  5 16:51 timer
```

# 系统设置
## 存储
数据盘用的btrfs，缓存层用的bcache  
创建存储池时没有选择文件系统  
![20251205175008.png](img/20251205175008.png)  
只能说中规中矩，不做评价
```text
NAME MAJ:MIN RM   SIZE RO TYPE  MOUNTPOINTS
sda    8:0    1     0B  0 disk
sdb    8:16   1     0B  0 disk
sdc    8:32   0 931.5G  0 disk
├─sdc1
│      8:33   0    90G  0 part
│ └─md0
│      9:0    0  89.9G  0 raid1
│   └─bcache0
│      252:0    0  89.9G  0 disk  /zettos/raid
├─sdc2
│      8:34   0    10G  0 part
│ └─md1
│      9:1    0    10G  0 raid1
│   └─bcache1
│      252:128  0    10G  0 disk  /zettos/raid/postgres-bcache
└─sdc3
       8:35   0 831.5G  0 part
  └─md2
       9:2    0 831.4G  0 raid1
    └─bcache2
       252:256  0 831.4G  0 disk
```
## 硬件设置
硬件设置较为简陋，仅有风扇档位、灯光、外接PS、显示IP的小屏幕 这几项控制选项  
至于网络启动、来电自启、内存压缩、定时开关机、来电自启，这些东西都是没有的，希望后续能加上  
![20251205181453.png](img/20251205181453.png)
## 时间
时间看起来不能自己设置，自动同步的选项是置灰的  
不知道断网的时候能不能自己设置时间  
![20251205181925.png](img/20251205181925.png)

时区处敏感地区使用了中性描述
![20251205181831.png](img/20251205181831.png)
## 文件服务
### SMB
SMB处仅有开关，没有SMB高级设置  
想设置SMB兼容性，是否加密是否开启WINS的开关是没有的  
信创的场景有难了  
![20251205182110.png](img/20251205182110.png)
### FTP
连端口都没得改，有内控内审的场景也是直接得拉满告警  
![20251205182353.png](img/20251205182353.png)
### WebDav
值得好评的一点是，这个WebDav直接访问可以列出文件目录  
当一个简单的WebServer用  
![20251205182534.png](img/20251205182534.png)
![20251205182546.png](img/20251205182546.png)
### NFS
NFS目前看起来不可用的样子
![20251205183033.png](img/20251205183033.png)

## 网络
这个网络状态-1Mb/s属于是偷懒了  
![20251205183158.png](img/20251205183158.png)

强制要求写DNS家里还是得请一下哈吉高了  
![20251205185601.png](img/20251205185601.png)

## 远程访问
DDNS服务仅支持阿里云、cloudflare、华为云、腾讯云  
不支持自定义配置  
![20251205185831.png](img/20251205185831.png)

这个IP地址，只能选择不能输入  
获取的IP地址是外网IP，想写内网IP访问的只能另想办法  
![20251205190028.png](img/20251205190028.png)

## 安全性
安全性只有证书上传这一项  
你防火墙呢，防火墙在哪里  
证书上传报错时没有明确的错误提示  
![20251205190237.png](img/20251205190237.png)

## 更新
系统更新未获取到更新时，没有当前版本展示  
![20251205190630.png](img/20251205190630.png)

## AI设置
只有一个开关还有一个学习时段  
![20251205190839.png](img/20251205190839.png)

# 应用商店
官方应用只有4个  
![20251205191008.png](img/20251205191008.png)  

其他的应用似乎是docker封装
![20251205191043.png](img/20251205191043.png)

没有第三方应用安装的入口，也没有第三方源可以选择
![20251205191213.png](img/20251205191213.png)

# ZettAI
没有明确的指引，也不知道具体能干什么  
具体情况如下，细品  
![20251205191508.png](img/20251205191508.png)
示例问题  
![20251205191455.png](img/20251205191455.png)
即兴发问
![20251205191435.png](img/20251205191435.png)
![20251205191541.png](img/20251205191541.png)

# 相册
十分简陋的相册，甚至连拖曳上传的功能都没有  
唯一的亮点，会通过OCR展示照片上的文字，虽然识别不是很准  
![20251205192037.png](img/20251205192037.png)

![20251205192055.png](img/20251205192055.png)

![20251205192324.png](img/20251205192324.png)

# 同步与备份
选取备份的时候可以直接快速选取排除范围，好评  
![20251205193442.png](img/20251205193442.png)

这个日期选择器就让人很难蹦得住了  
![20251205193517.png](img/20251205193517.png)

我自己备份到自己，就被归到备份到其他了
![20251205193915.png](img/20251205193915.png)

备份源路径与备份目标位置选择，交互十分割裂
![20251205194130.png](img/20251205194130.png)

# docker
创建docker-compose不能写volume  
相当于这个功能基本废了一半  
![20251205194549.png](img/20251205194549.png)

手动创建指定存储路径可以启动容器  
![20251205194727.png](img/20251205194727.png)
但我团队空间的团字去哪里了  
而且这个容器也进不去容器内shell  
遇见Jenkins那些创建容器时会把密码输出到某个文本文件的容器就废了  
![20251205194857.png](img/20251205194857.png)

支持冷门的macvlan好评
![20251205195258.png](img/20251205195258.png)

# 虚拟机
虚拟机只能选取3个核心，估计是异构核心启动不了虚拟机的问题没有去解决  
可能是偷懒没有定制qemu  
![20251205195505.png](img/20251205195505.png)

启动虚拟机时，需要的内存不会计算缓存的部分  
按Linux使用内存的激进程度，这个虚拟机基本上是起不来的  
![20251205195525.png](img/20251205195525.png)

支持内部nat好评  
![20251205195541.png](img/20251205195541.png)

没有汉化vnc界面  
![20251205200021.png](img/20251205200021.png)

导入镜像后，会在这个目录再复制存一份  
![20251205200239.png](img/20251205200239.png)
```text
root@Zettlab-D4:/zettos/pool/1/virtual_machine/vm/DATA/mirror# ls -lha
total 5.3G
drwxr-xr-x 1 root root   40 Dec  5 14:56 .
drwxr-xr-x 1 root root   34 Dec  5 14:53 ..
drwxr-xr-x 1 root root   64 Dec  5 14:41 recommend
-rw-r--r-- 1 root root 5.3G Dec  5 14:59 Windows.iso
```

# 结束语
评价为能用，期待后续迭代