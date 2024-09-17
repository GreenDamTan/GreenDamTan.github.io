# 在Proxmox VE 7.4 中开启NVIDIA P106显卡虚拟化(vGPU)

# 前言
本文使用硬件为Pascal架构的P106显卡，如您使用的显卡非本架构请自行变通

例如ampere架构的显卡需要自行开启VF，且每个VF仅能选择一个配置开启

文中提及的libvgpu_unlock_rs.so、NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run 、528.24_grid_win10_win11_server2019_server2022_dch_64bit_international.exe 均已上传至apaq镜像站中

宿主机驱动可见

https://mirrors.apqa.cn/vGPU/vgpu_unlock/drivers/NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run

libvgpu_unlock_rs.so可见，记得改名字

https://mirrors.apqa.cn/vGPU/vgpu_unlock/rust/libvgpu_unlock_rs_20230207_44d5bb3.so

以下本文使用的主要硬件及配置，如果您平台与本篇差距过大也请自行变通

主板：ASUS B150 PRO GAMING D3

CPU：Intel QQLT

内存：3条8G的DDR3绿条

存储：傲腾16G

所谓显卡：NVIDIA P106-090

网络存储：嗨群晖开启SMB及NFS

## 预编译/重打包文件
libvgpu_unlock_rs.so

该文件实际上并不需要每次装都编译一下，提前编译好就可以到处用

因为网络等原因，自己编译也挺困难的，直接用编译好的就行

NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run

该文件由官方驱动修改而来，也不需要每次都打一份，用改好的就行

## 关于PVE高版本版本
目前PVE8启用vGPU与PVE7并无区别，仅在添加设备处略有区别（详见下文"向虚拟机添加设备"）

PVE8宿主机驱动使用低版本vGPU宿主机驱动需要patch以支持高版本内核(会在文件名称显式2注明)

安装驱动时尽量选择注明版本与你内核版本相近的驱动

以如下Grid15.2为例，注意不要下错文件然后问为什么8版本的PVE用不了

NVIDIA-Linux-x86_64-525.105.14-vgpu-kvm-kernel-6.2.16-patched.run

纯vGPU宿主机驱动，适用于宿主机不需要跑涉N卡负载时使用

NVIDIA-Linux-x86_64-525.105.17-merged-vgpu-kvm-kernel-6.2.16-patched.run

杂种(merged)vGPU宿主机驱动，适用于宿主机在开vGPU时，仍然需要显示、视频转码、跑AI应用

宿主机的负载会占用显存，可能导致vGPU显存不足开不起来

具体案例请将查看疑难杂症一节

如果是17.0版本及以上，安装宿主机驱动请加上-m=kernel参数，详细情况可见“关于17.0版本以上”一节

# BIOS设置  
![230415155033.jpg](img/bios/230415155033.jpg)
## 开启VT
如图所示，开启VT功能  
![230415155103.jpg](img/bios/230415155103.jpg)  
## 开启VT-d
如图所示，开启VT-d  
![230415155116.jpg](img/bios/230415155116.jpg)  
## 开启主要输出为核显
不开启核显可能导致P106插上去后，就黑屏了

后续有兴趣你也可以顺便玩玩Intel的GVT-G  
![230415155148.jpg](img/bios/230415155148.jpg)  
## 安全引导设置
关闭安全引导，或者是选择非Windows系统  
![230415155313.jpg](img/bios/230415155313.jpg)  
# 安装PVE
略，这个有啥好说的，非本文重点好吧

# 查看系统信息
## 查看内核cmdline
```shell
cat /proc/cmdline
```  
此步骤可以迅速排查引导参数导致翻车的问题

如图所示，参数未有所需的  
![20230416005348.jpg](img/20230416005348.jpg)  
## 查看IOMMU状态
这一步不用命令行，直接在这个添加PCI设备看一眼就行

如图所示这种状态就是没开起来  
![20230416010515.jpg](img/20230416010515.jpg)  
## 查看显卡情况
使用`lspci -v -d 10DE:` 即可，此步骤防止你以为卡插好了实际上并没有的情况

如图所示，这是一张P106-090  
![20230416011602.jpg](img/20230416011602.jpg)  
# 修改相关配置及参数
## /etc/modules
```shell
echo -e "vfio\nvfio_iommu_type1\nvfio_pci\nvfio_virqfd" >> /etc/modules
```  
![20230416005850.jpg](img/20230416005850.jpg)
## /etc/modprobe.d/pve-blacklist.conf
```shell
echo "blacklist nouveau" >> /etc/modprobe.d/pve-blacklist.conf
```  
![20230416010012.jpg](img/20230416010012.jpg)
## 内核cmdline
此处需要注意不同启动方式需要修改的文件不同

本例为/etc/default/grub

添加intel_iommu=on iommu=pt spectre_v2=off到如图所示位置，spectre_v2参数非必须相关参数  
![20230416010427.jpg](img/20230416010427.jpg)  

> 有AMD用户反馈，参数需使用quiet amd_iommu=on iommu=pt pcie_acs_override=downstream
> 
> 并刷新grub及引导后重启才可以安装host驱动。
>
# 安装依赖及软件包
## 更换软件源
如下4行，执行便可  
```shell
sed -i 's|^deb http://ftp.debian.org|deb https://mirrors.ustc.edu.cn|g' /etc/apt/sources.list
sed -i 's|^deb http://security.debian.org|deb https://mirrors.ustc.edu.cn/debian-security|g' /etc/apt/sources.list
source /etc/os-release
echo "deb https://mirrors.ustc.edu.cn/proxmox/debian/pve $VERSION_CODENAME pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list

```  
## 安装包
一句`apt update` 即可  
![20230416005635.jpg](img/20230416005635.jpg)  
```log
root@pve:~# apt install -y build-essential dkms pve-headers-$(uname -r) mdevctl
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
The following additional packages will be installed:
  cpp cpp-10 dctrl-tools dpkg-dev fakeroot g++ g++-10 gcc gcc-10 jq libalgorithm-diff-perl libalgorithm-diff-xs-perl
  libalgorithm-merge-perl libasan6 libatomic1 libc-dev-bin libc-devtools libc6-dev libcc1-0 libcrypt-dev libdeflate0
  libdpkg-perl libfakeroot libfile-fcntllock-perl libgcc-10-dev libgd3 libgomp1 libisl23 libitm1 libjbig0 libjq1 liblsan0
  libmpc3 libmpfr6 libnsl-dev libonig5 libquadmath0 libstdc++-10-dev libtiff5 libtirpc-dev libtsan0 libubsan1 libwebp6 libxpm4
  linux-compiler-gcc-10-x86 linux-headers-5.10.0-21-amd64 linux-headers-5.10.0-21-common linux-headers-amd64 linux-kbuild-5.10
  linux-libc-dev lsb-release make manpages-dev patch pve-headers-5.15 pve-headers-5.15.102-1-pve sudo
Suggested packages:
  cpp-doc gcc-10-locales debtags menu debian-keyring g++-multilib g++-10-multilib gcc-10-doc gcc-multilib autoconf automake
  libtool flex bison gdb gcc-doc gcc-10-multilib glibc-doc git bzr libgd-tools libstdc++-10-doc make-doc ed diffutils-doc
The following NEW packages will be installed:
  build-essential cpp cpp-10 dctrl-tools dkms dpkg-dev fakeroot g++ g++-10 gcc gcc-10 jq libalgorithm-diff-perl
  libalgorithm-diff-xs-perl libalgorithm-merge-perl libasan6 libatomic1 libc-dev-bin libc-devtools libc6-dev libcc1-0
  libcrypt-dev libdeflate0 libdpkg-perl libfakeroot libfile-fcntllock-perl libgcc-10-dev libgd3 libgomp1 libisl23 libitm1
  libjbig0 libjq1 liblsan0 libmpc3 libmpfr6 libnsl-dev libonig5 libquadmath0 libstdc++-10-dev libtiff5 libtirpc-dev libtsan0
  libubsan1 libwebp6 libxpm4 linux-compiler-gcc-10-x86 linux-headers-5.10.0-21-amd64 linux-headers-5.10.0-21-common
  linux-headers-amd64 linux-kbuild-5.10 linux-libc-dev lsb-release make manpages-dev mdevctl patch pve-headers
  pve-headers-5.15 pve-headers-5.15.102-1-pve sudo
0 upgraded, 61 newly installed, 0 to remove and 18 not upgraded.

```  
## 刷新grub及引导
分别运行`update-grub`及`update-initramfs -u -k all` 即可，随后使用reboot重启  
```log
root@pve:~# update-grub
Generating grub configuration file ...
Found linux image: /boot/vmlinuz-5.15.102-1-pve
Found initrd image: /boot/initrd.img-5.15.102-1-pve
Found memtest86+ image: /boot/memtest86+.bin
Found memtest86+ multiboot image: /boot/memtest86+_multiboot.bin
Warning: os-prober will not be executed to detect other bootable partitions.
Systems on them will not be added to the GRUB boot configuration.
Check GRUB_DISABLE_OS_PROBER documentation entry.
Adding boot menu entry for UEFI Firmware Settings ...
done
root@pve:~# update-initramfs -u -k all
update-initramfs: Generating /boot/initrd.img-5.15.102-1-pve
Running hook script 'zz-proxmox-boot'..
Re-executing '/etc/kernel/postinst.d/zz-proxmox-boot' in new private mount namespace..
No /etc/kernel/proxmox-boot-uuids found, skipping ESP sync.
root@pve:~# reboot

```  
## 重启后检查
如下所示，cmdline应有刚才添加的内容，添加PCI设备也消去了警告  
```log
root@pve:~# cat /proc/cmdline 
BOOT_IMAGE=/boot/vmlinuz-5.15.102-1-pve root=UUID=61e72f0f-a4f7-4455-9959-aebf71e6af5e ro quiet intel_iommu=on iommu=pt spectre_v2=off

```  
![20230416012513.jpg](img/20230416012513.jpg)  

# host安装VGPU
## 创建相关配置
以下命令执行#号左边的就行比如说第一行就是`mkdir /etc/vgpu_unlock`  
```log
root@pve:# mkdir /etc/vgpu_unlock
root@pve:# touch /etc/vgpu_unlock/profile_override.toml
root@pve:# mkdir /etc/systemd/system/{nvidia-vgpud.service.d,nvidia-vgpu-mgr.service.d}
root@pve:# echo -e "[Service]\nEnvironment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so" > /etc/systemd/system/nvidia-vgpud.service.d/vgpu_unlock.conf
root@pve:# echo -e "[Service]\nEnvironment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so" > /etc/systemd/system/nvidia-vgpu-mgr.service.d/vgpu_unlock.conf
root@pve:# cat /etc/systemd/system/{nvidia-vgpud.service.d,nvidia-vgpu-mgr.service.d}/*
[Service]
Environment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so
[Service]
Environment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so

```  
## 上传必须文件
上传前需要通过  
```shell
mkdir -p /opt/vgpu_unlock-rs/target/release

```
创建对应文件夹  
随后通过ssh或者是其他方式上传`libvgpu_unlock_rs.so`及`NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run`  
![20230416012952.jpg](img/20230416012952.jpg)  
上传完了记得用chmod给run文件加可执行权限  

```log
root@pve:~# mkdir -p /opt/vgpu_unlock-rs/target/release
root@pve:~# cd /opt/vgpu_unlock-rs/target/release
root@pve:/opt/vgpu_unlock-rs/target/release# ls -l
total 96240
-rw-r--r-- 1 root root  4756296 Apr 16 01:29 libvgpu_unlock_rs.so
-rw-r--r-- 1 root root 93787315 Apr 16 01:29 NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run
root@pve:/opt/vgpu_unlock-rs/target/release# chmod +x NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run 
root@pve:/opt/vgpu_unlock-rs/target/release# ls -l
total 96240
-rw-r--r-- 1 root root  4756296 Apr 16 01:29 libvgpu_unlock_rs.so
-rwxr-xr-x 1 root root 93787315 Apr 16 01:29 NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run

```
## 安装host驱动
一句./NVIDIA-Linux-x86_64-525.85.07-vgpu-kvm-patched.run 就跑起来了  
![20230416013358.jpg](img/20230416013358.jpg)  
![20230416013409.jpg](img/20230416013409.jpg)  
![20230416013426.jpg](img/20230416013426.jpg)  
## 检查安装情况
保险起见可以先使用以下命令启动相关服务
```shell
systemctl restart {nvidia-vgpud.service,nvidia-vgpu-mgr.service} 
```  

随后使用nvidia-smi以及mdevctl types查看  
![20230416013500.jpg](img/20230416013500.jpg)  
![20230416013610.jpg](img/20230416013610.jpg)  
# guest安装VGPU
## 向虚拟机添加设备
首先给你要跑gpu的虚拟机添加设备