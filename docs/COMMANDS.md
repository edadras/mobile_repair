# mrt command reference

Global options (before the group):

| option | meaning |
|---|---|
| `-s, --serial SERIAL` | target device (adb or fastboot serial) |
| `--adb PATH`, `--fastboot PATH` | binaries (also `MRT_ADB`, `MRT_FASTBOOT`) |
| `-y, --yes` | auto-accept safety prompts (scripting only) |
| `-n, --dry-run` | print host commands, execute nothing |
| `--json` | JSON output |
| `-v, --verbose` | echo every host command |
| `--log-dir DIR`, `--no-log` | operation log location (`~/.mrt/logs`, `$MRT_LOG_DIR`) |
| `--no-adb-root` | never try `adb root`, only `su` |

Exit codes: 0 ok · 1 generic · 2 tool not found · 3 device not found · 4 command failed · 5 root required · 6 aborted by operator · 7 verification failed · 8 unsupported format.

## Top level

| command | description |
|---|---|
| `mrt devices` | list adb + fastboot devices with mode |
| `mrt doctor` | check adb/fastboot versions, udev/driver hints, devices, log dir |
| `mrt menu` | interactive bilingual menu |
| `mrt info [--full] [--imei] [--save FILE]` | identity, kernel, CPU, memory, storage, battery, display, network, security (root/SELinux/verified boot/encryption/FRP/OEM unlock), slots, users, SIM. In fastboot mode: `getvar all` summary + partition table |
| `mrt props [pattern]` | `getprop` (filtered) |
| `mrt shell [--root] [cmd...]` | run a command (root via `adb root`/`su`), interactive if empty |
| `mrt reboot {system,recovery,bootloader,fastboot,fastbootd,sideload,sideload-auto-reboot,edl,download,safemode}` | works from adb or fastboot mode |
| `mrt wait {device,recovery,sideload,fastboot,bootloader} [--timeout]` | wait for a mode |

## `mrt fastboot` (alias `fb`)

| command | description |
|---|---|
| `getvar [name]` | bootloader variables (default `all`) |
| `partitions` | partition table (`partition-size:*`) as a table |
| `flash PART IMAGE [--slot a|b|all]` | flash; firmware partitions (abl/xbl/tz/modem/…) require typing the partition name |
| `erase PART` / `format PART [--fs ext4]` | erase / format |
| `boot IMAGE` | boot an image without flashing (TWRP, test kernels) |
| `reboot [system|bootloader|recovery|fastboot]` | |
| `unlock [--method auto|flashing|oem] [--critical]` | `flashing unlock` then `oem unlock`; token `UNLOCK` |
| `lock [...]` | token `LOCK` (warns about bricking with non-stock ROM) |
| `set-active a|b` | switch slot |
| `oem ARGS...` | `fastboot oem …` |
| `wipe` | `fastboot -w` (token `WIPE`) |
| `continue` | continue boot |
| `raw ARGS...` | any raw fastboot command |

## `mrt part` (alias `partition`) — raw dd access, needs root

| command | description |
|---|---|
| `list` | partitions with sizes from `/dev/block/by-name` (+ bootdevice/platform paths) and `/sys/class/block/*/size` |
| `table` | block devices, slot suffix, `lpdump` of the super partition |
| `dump NAME [OUT] [--no-verify] [--bs 4M]` | `adb exec-out dd if=/dev/block/by-name/NAME` streamed to the host file, then `sha256sum` on device vs host. `NAME` may be `boot`, `boot_a` (slot suffix auto-resolved) or a `/dev/block/...` path |
| `dump-range NAME OUT --offset N --length N` | byte-granular read (`K/M/G`, `0x..` accepted) |
| `dump-all DIR [--include a,b] [--exclude a,b] [--max-size 512M] [--with-data] [--no-verify]` | every partition to `DIR/NAME.img` + `manifest.json` (sizes, sha256). By default skips userdata/super/system/vendor/product/odm/cache/metadata |
| `write NAME IMAGE [--no-verify] [--exact-size] [--bs 4M]` | size check → push to `/data/local/tmp` → hash the upload → `dd of=/dev/block/by-name/NAME conv=fsync` → hash the partition again. Token: partition name |
| `compare NAME IMAGE` | sha256 of the first `len(IMAGE)` bytes of the partition vs the image; exit 1 on mismatch |
| `wipe NAME` | `dd if=/dev/zero` (token: partition name) |

## `mrt backup`

| command | description |
|---|---|
| `create [DIR] [--no-apps] [--all-apps] [--app-data] [--sdcard] [--sdcard-path P] [--partitions a,b] [--critical-partitions] [--all-partitions] [--with-userdata]` | writes `device-info.json`, `apps/<pkg>/*.apk`, `apps.json`, `appdata.ab`, `sdcard/`, `partitions/*.img + manifest.json`, `manifest.json`. `--critical-partitions` = boot, recovery, dtbo, vbmeta*, vendor_boot, init_boot, persist, efs, modemst1/2, fsg, fsc, nvram, nvdata, nvcfg, proinfo, sec_efs, frp, config, misc, modem, bluetooth, dsp, abl, xbl, xbl_config (those that exist) |
| `restore DIR [--apps] [--app-data] [--sdcard] [--partitions a,b]` | warns (token `MISMATCH`) if the backup came from a different device model |
| `verify DIR` | re-hash partition images against the manifest |

## `mrt rom`

| command | description |
|---|---|
| `inspect PATH` | identify: fastboot image dir, A/B OTA zip (payload.bin), recovery flashable zip, factory zip, raw image, Odin tar — and print the matching mrt command |
| `flash-dir DIR [--slot] [--only a,b] [--skip a,b] [--wipe] [--with-data] [--reboot] [--disable-verity] [--disable-verification]` | flashes every `*.img` in the correct order (firmware → vbmeta → boot chain → super/dynamic → …), reboots the bootloader after bootloader/radio, switches to fastbootd for logical partitions, stops at the first failure. userdata/persist/efs/… are skipped unless `--with-data`. Token `FLASH` |
| `payload SOURCE [--workdir DIR] [--extract-only] [--only a,b] + flash-dir options` | extract images from `payload.bin` or an OTA zip (REPLACE / REPLACE_BZ / REPLACE_XZ / ZERO ops; full OTA only) then flash |
| `payload-info SOURCE` | list partitions inside a payload |
| `update-zip ZIP [--wipe]` | `fastboot update ZIP` (factory images) |
| `sideload ZIP [--no-auto-reboot]` | same as `recovery sideload` |

## `mrt recovery` (alias `rec`)

| command | description |
|---|---|
| `status` | current mode of connected devices |
| `sideload ZIP [--no-auto-reboot]` | reboots to sideload from any mode, waits, streams `adb sideload` |
| `wipe {cache,data,dalvik,system}` | TWRP `twrp wipe`, stock `recovery --wipe_*`, or root cleanup from system |
| `factory-reset` | recovery `--wipe_data`, root `/cache/recovery/command`, or `MASTER_CLEAR` broadcast |
| `flash IMAGE [--slot] [--boot]` | flash a custom recovery via fastboot, optionally reboot into it |
| `push FILES... [--to /sdcard/]` | push ROM zips / Magisk to the device (works in TWRP) |
| `twrp ARGS...` | `twrp install|wipe|backup|restore|sideload …` |

## `mrt apps` (alias `app`, `pm`)

| command | description |
|---|---|
| `list [--filter all|third|system|disabled|enabled|uninstalled] [--user N] [--versions] [--grep TEXT]` | |
| `info PKG` | version, install times, installer, sdk, paths, enabled state, runtime permissions, APK paths |
| `install FILES... [--no-reinstall] [--downgrade] [--no-grant] [--test] [--user N]` | split APKs via `install-multiple` |
| `uninstall PKG [--keep-data] [--user N] [--system]` | `--user 0` removes bloatware without root |
| `disable PKG` / `enable PKG [--user N]` | `pm disable-user` / `pm enable` |
| `clear PKG` | `pm clear` |
| `stop PKG` | `am force-stop` |
| `pull PKG [--out DIR]` | pull base + split APKs |
| `backup PKG FILE.ab [--no-apk]` / `restore FILE.ab` | `adb backup`/`adb restore` |
| `grant PKG PERM` / `revoke PKG PERM` | runtime permissions |
| `debloat PKGS_OR_LISTFILE... [--mode disable|uninstall|enable|reinstall] [--user N]` | batch; `reinstall` uses `cmd package install-existing` |
| `ps [--top N]` | running processes |

## `mrt logs` (alias `log`)

| command | description |
|---|---|
| `logcat [-o FILE] [-b all] [-f] [-c] [FILTERS...]` | dump (default) or follow |
| `dmesg [-o FILE]` | kernel log (root) |
| `last-kmsg DIR` | pull `/sys/fs/pstore/*` and `/proc/last_kmsg` — the previous boot's crash log |
| `bugreport [OUT.zip]` | |
| `dumpsys [SERVICE] [ARGS...] [-o FILE]` | |
| `crashes DIR` | `/data/anr`, `/data/tombstones`, `/data/system/dropbox` as tar (root) |
| `collect [DIR]` | logcat + dmesg + 14 dumpsys services + getprop + pstore + crashes + bugreport |
| `battery` | `batterystats --charged` |
| `boot-reason` | `ro.boot.bootreason`, boot count, uptime |

## `mrt screen`

`shot [OUT.png]`, `record [OUT.mp4] [--seconds N] [--size WxH] [--bitrate N]`, `tap X Y`, `swipe X1 Y1 X2 Y2 [--ms]`, `key KEYCODE`, `text ...`, `touch-test [--seconds N]` (raw `getevent`), `unlock [--pin PIN]`.

## Root handling

`mrt` probes in this order and caches the result: plain `adb shell id` (already root, e.g. recovery/userdebug) → `adb root` (unless `--no-adb-root`) → `su -c id` (Magisk/SuperSU) → `su 0 id`. Commands are wrapped with `su -c '<quoted cmd>'` when needed, both for `adb shell` and `adb exec-out` streams.

## Operation log

`~/.mrt/logs/mrt-YYYYMMDD-HHMMSS.log` (text) and `.jsonl` (one JSON object per event: `command` with argv/rc/duration/stderr, `info`, `warn`, `error`, confirmations and their outcome). Use `--log-dir` or `MRT_LOG_DIR` to change the location.
