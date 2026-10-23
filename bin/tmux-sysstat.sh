#!/usr/bin/env bash
# tmux 右下角的「CPU / 内存 / 交换 / 磁盘」一小段。tmux 每 status-interval 秒调一次，所以要快：
# 不用 top（一次 1.5 秒），CPU 用 iostat（1 秒，后台跑不卡界面），内存读 vm_stat。
# 内存口径同活动监视器的「已使用」= 匿名页 + 联动（wired）+ 压缩器占用，实测和 top 的 PhysMem used 对得上。
# 颜色：绿 = 够用，橙 = 偏紧，红 = 快撑不住（卡的时候交换和内存会先红）。
set -u
G='#[fg=#4cc27a]'; Y='#[fg=#e3a04a]'; R='#[fg=#ff5f57]'; D='#[default]'
col() { # col <值> <橙线> <红线> ：值越大越糟
  awk -v v="$1" -v y="$2" -v r="$3" -v G="$G" -v Y="$Y" -v R="$R" 'BEGIN{print (v>=r?R:(v>=y?Y:G))}'
}
ncpu=$(sysctl -n hw.ncpu)
# CPU：iostat -c 2 取两次采样（约 1 秒）算 100 - 空闲%，和 top / tmux-cpu 插件同口径（实测与 top 一致，
# ps 把各进程的 %CPU 相加会偏低一半）。tmux 的 #() 是后台跑的，等这 1 秒不会卡界面。iostat 不在就退回 ps。
cpu=$(iostat -c 2 disk0 2>/dev/null | sed '/^\s*$/d' | tail -1 | awk '{v=100-$6; if(v<0||v>100||$6=="")exit 1; printf "%d", v}') \
  || cpu=$(ps -A -o %cpu= | awk -v n="$ncpu" '{s+=$1} END{v=s/n; if(v>100)v=100; printf "%d", v}')
read -r anon wired comp < <(vm_stat | awk '/Anonymous pages/{a=$3} /wired down/{w=$4} /occupied by compressor/{c=$5} END{gsub(/\./,"",a);gsub(/\./,"",w);gsub(/\./,"",c);print a,w,c}')
total=$(sysctl -n hw.memsize)
used_g=$(awk -v a="$anon" -v w="$wired" -v c="$comp" 'BEGIN{printf "%.0f", (a+w+c)*16384/1073741824}')
tot_g=$(awk -v t="$total" 'BEGIN{printf "%.0f", t/1073741824}')
swap_g=$(sysctl -n vm.swapusage | awk '{for(i=1;i<=NF;i++) if($i=="used") {v=$(i+2); sub(/M/,"",v); printf "%.1f", v/1024}}')
disk_g=$(df -k /System/Volumes/Data | awk 'NR==2{printf "%d", $4/1048576}')
pct=$(awk -v u="$used_g" -v t="$tot_g" 'BEGIN{printf "%d", u*100/t}')
dcol=$(awk -v v="$disk_g" -v G="$G" -v Y="$Y" -v R="$R" 'BEGIN{print (v<=20?R:(v<=40?Y:G))}')
# 图标是 Nerd Font 的（iTerm 字体 MesloLGSDZNF 自带；iTerm 里关掉「非 ASCII 单独用别的字体」才画得出来，
# 否则私有区字符会被交给没有它的中文字体，显示成问号方块）：
#   U+F0E4 仪表盘 = CPU，U+EAD7 芯片 = 内存，U+F0EC 对调箭头 = 交换，U+F0A0 硬盘 = 磁盘余量
# macOS 自带 bash 3.2 不认 $'\uXXXX'，所以写成 UTF-8 字节
ic_cpu=$(printf '\357\203\244'); ic_mem=$(printf '\356\253\227'); ic_swap=$(printf '\357\203\254'); ic_disk=$(printf '\357\202\240')
# 每一段是自己的点击范围（tool:sys-cpu / sys-mem / sys-swap / sys-disk），点哪段弹哪段的小窗（float-tool.sh）。
# 范围里前后各留一格空格：点偏一格 tmux 就找不到范围（status-badge.sh 里同样的教训）。
seg() { printf '#[range=user|tool:%s] %s%s%s #[norange]' "$1" "$2" "$3" "$D"; }
seg sys-cpu  "$(col "$cpu" 60 85)" "${ic_cpu} ${cpu}%"
seg sys-mem  "$(col "$pct" 80 92)" "${ic_mem} ${used_g}/${tot_g}G"
seg sys-swap "$(col "$swap_g" 2 4)" "${ic_swap} ${swap_g}G"
seg sys-disk "$dcol" "${ic_disk} ${disk_g}G"
