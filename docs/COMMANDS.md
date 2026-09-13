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

## `mrt root` — root with Magisk

Host-driven version of what the Magisk app does. Needs the Magisk APK on the host (download it from the official Magisk releases), an **unlocked bootloader** for the flash step (`mrt fastboot unlock`, erases data) and the stock `boot`/`init_boot` image of the **installed** firmware build.

| command | description |
|---|---|
| `status` | prerequisites: ABI, A/B slot, target partition (`init_boot` on Android 13+ GKI devices, else `boot`), bootloader lock state (from `ro.boot.verifiedbootstate` / `ro.boot.flash.locked` / `ro.boot.vbmeta.device_state`), existing root, Magisk app presence, and whether the flash step goes through fastboot or (Samsung) Odin |
| `patch --magisk APK (--boot-image IMG \| --payload OTA) [--target auto\|boot\|init_boot] [--out DIR] [--no-keep-verity] [--no-keep-forceencrypt] [--patch-vbmeta] [--recovery-mode] [--install-app]` | copies the stock image (from the file, extracted from an OTA `payload.bin`, or dumped from an already-rooted device), pushes Magisk's own `magiskboot`/`magiskinit`/`boot_patch.sh` (extracted from the APK for the device ABI) to `/data/local/tmp`, runs `boot_patch.sh` on the device with the same env vars the app uses and pulls `new-boot.img` back as `magisk_patched-<target>.img`. Writes `stock-<target>.img` and `root-manifest.json` (sha256 of both images, device fingerprint, Magisk version, patch log) into `DIR` |
| `flash DIR\|IMAGE [--target boot\|init_boot] [--temporary] [--slot a\|b\|all] [--no-reboot]` | reboots to the bootloader if needed, refuses when `unlocked: no`, verifies the image sha256 against the manifest and the bootloader `product` against the manifest (`MISMATCH` token to override), then `fastboot flash <target>` (token = partition name) and reboots. `--temporary` does `fastboot boot` instead: root for one boot only (finish with "Direct Install" in the Magisk app) |
| `install --magisk APK ... [--temporary] [--slot S]` | `patch` followed by `flash` |
| `unroot DIR\|IMAGE [--slot S] [--no-reboot]` | flashes the saved stock image back |

Samsung: `patch` works (use `--recovery-mode` / `--patch-vbmeta` as the Magisk docs describe for the model), but the flash step must be done with Odin (AP tar) — `flash` refuses on Samsung.

## `mrt imei` — IMEI service layer

A single front end that selects the chipset backend and knows its transport and storage:

```
                     IMEI Service Layer
                             │
       ┌─────────────────────┼─────────────────────┐
       ↓                     ↓                     ↓
   Qualcomm                 MTK                 Samsung
     DIAG                  META                Service/AT
       │                     │                     │
       ↓                     ↓                     ↓
     NV/QCN            NVRAM/NVDATA               EFS/NV
```

Reading works over adb (Android telephony) and offline (QCN NV item 550). A **live write** needs the chipset's proprietary port (Qualcomm DIAG, MediaTek META, or the modem AT channel), none reachable over adb — so `mrt` writes the IMEI **offline inside a QCN** (restore with QPST) and, for a live write, emits the exact payload. Writing an IMEI is a licensed repair operation to restore a device's own number; the number is always supplied and Luhn-validated, never invented.

| command | description |
|---|---|
| `layers [--chipset C] [--device]` | print the transport/storage map; `--device` detects the chipset from the phone |
| `read [--chipset C]` | live IMEI through the selected layer (Android telephony), with chipset/transport/storage reported |
| `check IMEI` | validate a 15-digit IMEI (Luhn) or complete a 14-digit one |
| `encode IMEI` | show the NV_UE_IMEI (item 550) value, the DIAG write frame and the AT+EGMR command |
| `decode HEX` | decode a packed NV_UE_IMEI value back to an IMEI |
| `plan-write IMEI [--chipset C] [--sim 1\|2]` | the transport payload for a live write (DIAG frame / NVRAM value / AT command) — does not send anything |
| `qcn-read FILE [--item N] [--storage S]` | read the IMEI from NV item 550 in a QCN (offline) |
| `qcn-write FILE IMEI [--item N] [--storage S] [--out FILE2]` | write the IMEI into NV item 550 in a QCN, preserving the stream length. Token `IMEI`. Flash the QCN back with QPST/QFIL |

## `mrt efs` (alias `nv`) — EFS / NV: IMEI, MAC, RF calibration

These partitions carry radio identity. Handle them as one atomic group; back up before touching anything. Partition operations need root.

| command | description |
|---|---|
| `detect` | detect chipset (Qualcomm/MediaTek/Samsung) and list which EFS/NV partitions exist per group |
| `backup [DIR] [--group modem-nv|persist|modem-fw] [--chipset auto|qualcomm|mediatek|samsung] [--no-efs-fs]` | dump the whole group hash-verified into `DIR` with `efs-manifest.json`; on Samsung also tars `/efs`. Default group `modem-nv` = Qualcomm `modemst1,modemst2,fsg,fsc` / MediaTek `nvram,nvdata,nvcfg,protect1,protect2` / Samsung `efs,sec_efs,cpefs,…` |
| `restore DIR [--group G] [--partitions a,b] [--no-rollback]` | atomic restore. Guards against a different device model (token `MISMATCH`), warns if you restore only one of the `modemst1`/`modemst2` mirror pair or omit `fsg`, dumps current content to a `rollback-*` folder first, writes each image with `part write` (size + hash verified), token `RESTORE` |
| `validate [--group G] [--chipset C] [--no-deep]` | per-partition state (populated vs erased/empty), `modemst1` vs `modemst2` mirror comparison, Samsung md5-sidecar status, and (default, `--no-deep` to skip) an internal-structure check of every partition ≤ 64 MiB with a known format: EFS2 superblocks/geometry/live `age` for `modemst1/2`/`fsg`, ext4 superblock + CRC32C for `nvdata`/`nvcfg`, backup-region header + file table for `nvram`, plus a `structure_summary` with mirror/fsg conclusions |
| `check-image PATH...` | the same structure check **offline** on dumped `.img` files or whole `mrt efs backup` directories: format detection, expected-format mismatch, EFS2 mirror comparison (which copy is newest, is `fsg` usable), MediaTek backup usability |
| `rebuild-modemst [--backup-dir DIR] [--reboot]` | **Qualcomm**: takes a verified backup of the group, refuses unless `fsg` parses as a valid EFS2 image, then zero-fills `modemst1` + `modemst2` so the modem re-creates them from `fsg` on the next boot. Token `REBUILD`. Run from recovery (or use `--reboot`) so the running modem cannot write its cache back |
| `mtk-rebuild-nvdata [--backup-dir DIR] [--reboot]` | **MediaTek**: takes a verified backup, refuses unless the `nvram` backup region parses and lists files, unmounts `/nvdata`, re-creates it as an empty ext4 (`mke2fs` on the device; zero-fill fallback) so `nvram_daemon` restores every NVRAM file from the `nvram` backup on the next boot. Token `REBUILD` |
| `samsung-fix-md5 [--efs-dir /efs] [--local DIR] [--create]` | recompute Samsung `nv_data.bin.md5` (and `.nv_data.bak.md5`, …) so the sidecar matches the current binary. Device mode rewrites sidecars as the base file's owner; `--local` fixes a pulled copy on the host; `--create` also creates missing sidecars for known files |
| `nv-crc [--hex H | --file F]` | compute the DIAG CRC-16/X-25 and MD5 of a value |
| `qcn info FILE` | list storages and NV items inside a QCN (QPST backup) |
| `qcn extract FILE [--item N] [--storage S] [--out FILE|DIR]` | extract one item (to file or hex/ascii) or every item into a directory |
| `qcn edit FILE ITEM (--value HEX | --value-ascii STR | --value-file F) [--storage S] [--out FILE2] [--rebuild]` | edit an NV item value **inside the QCN file**. Same length → safe in-place byte edit (whole container preserved); different length or new item → `--rebuild`. Token: destructive confirm |
| `qcn diff A B` | compare the NV items of two QCN files (added/removed/changed) |

### What is and isn't a real checksum fix

* **Samsung `nv_data.bin.md5`** is a genuine, documented, recomputable checksum — `samsung-fix-md5` regenerates it correctly.
* **QCN** streams hold *raw* NV values; `qcn edit` rewrites them and (for length changes) rebuilds a valid Compound File. The DIAG per-item CRC-16 is a wire checksum applied when writing to the modem, not stored in the QCN, so no checksum needs "fixing" inside the file.
* **Qualcomm `modemst1/2`/`fsg`** hold an EFS2 log-structured filesystem. Its superblock (`EFSSuper`, page header / version / `age` / geometry / NAND info) contains **no CRC field**; integrity is kept by the modem through the log sequence, the `modemst1`/`modemst2` mirror and the `fsg` golden copy. There is nothing a host tool can "recompute". What `mrt` does instead is real: `validate` / `check-image` parse every superblock and verify geometry and the live `age`, and `rebuild-modemst` makes the **modem itself** regenerate the mirror from `fsg` (after a backup and only when `fsg` verifies). Otherwise use `efs restore` of the whole group from a good dump of the **same** device.
* **MediaTek `nvdata`/`nvcfg`** are plain ext4 filesystems (the kernel owns their metadata checksums; `check-image` verifies the superblock CRC32C when `metadata_csum` is enabled) and **`nvram`** is the `nvram_daemon` backup region (`File_Title_Header` with `BackupFlag = 0xFECF`, a file table and the file bodies). No hand-recomputable partition checksum exists; the daemon re-validates and restores its files at boot. `mtk-rebuild-nvdata` uses exactly that mechanism: empty `nvdata` (after a backup and only when the `nvram` backup verifies) and let `nvram_daemon` restore it. Otherwise use `efs restore`.
* **Writing NV items or a QCN back onto the modem** requires a DIAG port (QPST/QFIL). It cannot be done over adb; `mrt` edits QCN files offline and restores partitions with `dd`, it does not talk the DIAG protocol.
