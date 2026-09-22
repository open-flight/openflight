INSTALLER_DIR ?= ti_installers
TI_ROOT ?= /opt/ti
MMWAVE_SDK_INSTALL_PATH ?= $(TI_ROOT)/sdk/mmwave_sdk_03_06_02_00-LTS/packages
R4F_CODEGEN_INSTALL_PATH ?= $(TI_ROOT)/cgt-arm/ti-cgt-arm_20.2.7.LTS
XDC_INSTALL_PATH ?= $(TI_ROOT)/xdc/xdctools_3_61_00_16_core
BIOS_INSTALL_PATH ?= $(TI_ROOT)/bios/bios_6_73_01_01/packages
SYSCONFIG_INSTALL_PATH ?= $(TI_ROOT)/sysconfig
XWR68XX_RADARSS_IMAGE_BIN ?= $(TI_ROOT)/sdk/mmwave_sdk_03_06_02_00-LTS/firmware/radarss/xwr6xxx_radarss_rprc.bin
FIRMWARE_DIR ?= iwr6843
REPO_ROOT := $(abspath $(CURDIR)/..)
DOCKER_IMAGE ?= openflight-iwr-sdk:latest
RELEASE_DIR ?= releases
RELEASE_NAME ?= l3_dump_configurable_capture_20260818.bin

SDK_INSTALLER := $(INSTALLER_DIR)/mmwave_sdk_03_06_02_00-LTS-Linux-x86-Install.bin
R4F_INSTALLER := $(INSTALLER_DIR)/ti_cgt_tms470_20.2.7.LTS_linux-x64_installer.bin
BIOS_INSTALLER := $(INSTALLER_DIR)/bios_6_73_01_01.run
SYSCONFIG_INSTALLER := $(INSTALLER_DIR)/sysconfig-1.10.0_2163-setup.run
XDC_ZIP := $(INSTALLER_DIR)/xdctools_3_61_00_16_core_linux.zip

BUILD_ENV := \
	MMWAVE_SDK_DEVICE=iwr68xx \
	DOWNLOAD_FROM_CCS=yes \
	MMWAVE_SDK_INSTALL_PATH=$(MMWAVE_SDK_INSTALL_PATH) \
	R4F_CODEGEN_INSTALL_PATH=$(R4F_CODEGEN_INSTALL_PATH) \
	XDC_INSTALL_PATH=$(XDC_INSTALL_PATH) \
	BIOS_INSTALL_PATH=$(BIOS_INSTALL_PATH) \
	XWR68XX_RADARSS_IMAGE_BIN=$(XWR68XX_RADARSS_IMAGE_BIN)

# Directly fetchable installers: verified 2026-07-27 to return real binaries
# (ELF / ZIP magic) rather than a TI login or export-control HTML page.
FETCHABLE_INSTALLERS := \
	'bios_6_73_01_01.run|https://software-dl.ti.com/dsps/dsps_public_sw/sdo_sb/targetcontent/bios/sysbios/6_73_01_01/exports/bios_6_73_01_01.run' \
	'sysconfig-1.10.0_2163-setup.run|https://software-dl.ti.com/ccs/esd/sysconfig/sysconfig-1.10.0_2163-setup.run' \
	'xdctools_3_61_00_16_core_linux.zip|https://software-dl.ti.com/dsps/dsps_public_sw/sdo_sb/targetcontent/rtsc/3_61_00_16/exports/xdccore/xdctools_3_61_00_16_core_linux.zip'

# These two sit behind a TI login plus an export-control click-through, so they
# cannot be scripted. A direct GET returns an HTML consent page.
MANUAL_INSTALLERS := \
	'mmwave_sdk_03_06_02_00-LTS-Linux-x86-Install.bin|https://www.ti.com/tool/MMWAVE-SDK' \
	'ti_cgt_tms470_20.2.7.LTS_linux-x64_installer.bin|https://www.ti.com/tool/ARM-CGT'

REQUIRED_INSTALLERS := \
	mmwave_sdk_03_06_02_00-LTS-Linux-x86-Install.bin \
	ti_cgt_tms470_20.2.7.LTS_linux-x64_installer.bin \
	bios_6_73_01_01.run \
	sysconfig-1.10.0_2163-setup.run \
	xdctools_3_61_00_16_core_linux.zip

.PHONY: help check-host check-tools check-installers install-ti-deps-native install-ti-tools-native probe-installers-native fetch-installers build-native docker-image docker-build docker-shell clean

help:
	@echo "IWR6843 production firmware helpers"
	@echo ""
	@echo "Targets:"
	@echo "  check-host              Verify the native x86_64 Linux build host"
	@echo "  check-tools             Verify the host AND the installed TI toolchain"
	@echo "  check-installers        Verify required TI installers are present"
	@echo "  fetch-installers        Download the 3 unauthenticated TI installers"
	@echo "  install-ti-deps-native  Install Debian packages required by TI tools"
	@echo "  probe-installers-native Verify the downloaded installer executables"
	@echo "  install-ti-tools-native Install TI SDK/toolchain under TI_ROOT"
	@echo "  build-native            Build the configurable IQ16/IQ8 production firmware"
	@echo ""
	@echo "  docker-image            Build the x86_64 build container (needs TI installers)"
	@echo "  docker-build            Build the configurable firmware inside the container"
	@echo "  docker-shell            Interactive shell in the build container"
	@echo "  clean                   Remove generated TI build outputs"

check-host:
	@set -eu; \
	echo "host_arch=$$(uname -m)"; \
	echo "page_size=$$(getconf PAGE_SIZE)"; \
	test "$$(uname -m)" = "x86_64"

check-installers:
	@set -eu; \
	missing=0; \
	for file in $(REQUIRED_INSTALLERS); do \
		if [ ! -f "$(INSTALLER_DIR)/$$file" ]; then \
			echo "missing: $(INSTALLER_DIR)/$$file"; \
			missing=1; \
		else \
			echo "found:   $(INSTALLER_DIR)/$$file"; \
		fi; \
	done; \
	exit $$missing

install-ti-deps-native:
	sudo dpkg --add-architecture i386
	sudo apt-get update
	sudo apt-get install -y build-essential make mono-runtime libc6:i386 libstdc++6:i386 zlib1g:i386 bison flex unzip rsync file

probe-installers-native: check-installers
	@set -u; \
	chmod +x "$(SDK_INSTALLER)" "$(R4F_INSTALLER)" "$(BIOS_INSTALLER)" "$(SYSCONFIG_INSTALLER)"; \
	for installer in "$(SDK_INSTALLER)" "$(R4F_INSTALLER)" "$(BIOS_INSTALLER)" "$(SYSCONFIG_INSTALLER)"; do \
		echo "=== $$installer --help ==="; \
		"$$installer" --help >/tmp/installer-help.txt 2>&1; \
		rc=$$?; \
		echo "exit=$$rc"; \
		sed -n '1,24p' /tmp/installer-help.txt; \
	done

install-ti-tools-native: check-installers
	sudo mkdir -p "$(TI_ROOT)/sdk" "$(TI_ROOT)/cgt-arm" "$(TI_ROOT)/bios" "$(TI_ROOT)/xdc" "$(SYSCONFIG_INSTALL_PATH)"
	chmod +x "$(SDK_INSTALLER)" "$(R4F_INSTALLER)" "$(BIOS_INSTALLER)" "$(SYSCONFIG_INSTALLER)"
	sudo "$(SDK_INSTALLER)" --mode unattended --prefix "$(TI_ROOT)/sdk" --disable-components TI_CGT_C6000,TI_CGT_ARM,DSPLIB_C64Px,DSPLIB_C674x,MATHLIB_C674x,XDCtools,SYS_BIOS
	sudo "$(R4F_INSTALLER)" --mode unattended --prefix "$(TI_ROOT)/cgt-arm"
	sudo "$(BIOS_INSTALLER)" --mode unattended --prefix "$(TI_ROOT)/bios"
	sudo "$(SYSCONFIG_INSTALLER)" --mode unattended --prefix "$(SYSCONFIG_INSTALL_PATH)"
	sudo unzip -q -o "$(XDC_ZIP)" -d "$(TI_ROOT)/xdc"
	test -d "$(MMWAVE_SDK_INSTALL_PATH)"
	test -x "$(R4F_CODEGEN_INSTALL_PATH)/bin/armcl"
	test -d "$(BIOS_INSTALL_PATH)"
	test -x "$(XDC_INSTALL_PATH)/xdc"
	test -f "$(XWR68XX_RADARSS_IMAGE_BIN)"

check-tools:
	@fail=0; \
	if [ "$$(uname -s)" != "Linux" ] || [ "$$(uname -m)" != "x86_64" ]; then \
		echo "ERROR: the TI mmWave SDK and ARM codegen tools are Linux x86_64 only."; \
		echo "       this host is $$(uname -s) $$(uname -m)."; \
		echo "       Build inside the x86_64 Linux VM or container; see"; \
		echo "       firmware/README.md, 'Build On Native x86_64 Linux'."; \
		fail=1; \
	fi; \
	for dir in "$(MMWAVE_SDK_INSTALL_PATH)" "$(BIOS_INSTALL_PATH)"; do \
		if [ ! -d "$$dir" ]; then echo "ERROR: missing directory $$dir"; fail=1; fi; \
	done; \
	for exe in "$(R4F_CODEGEN_INSTALL_PATH)/bin/armcl" "$(XDC_INSTALL_PATH)/xdc"; do \
		if [ ! -x "$$exe" ]; then echo "ERROR: missing executable $$exe"; fail=1; fi; \
	done; \
	if [ ! -f "$(XWR68XX_RADARSS_IMAGE_BIN)" ]; then \
		echo "ERROR: missing RADARSS image $(XWR68XX_RADARSS_IMAGE_BIN)"; fail=1; \
	fi; \
	if [ "$$fail" != "0" ]; then \
		echo ""; \
		echo "On an x86_64 Linux host, install the toolchain with:"; \
		echo "  make -C firmware install-ti-tools-native"; \
		echo "Or point TI_ROOT at an existing install: make -C firmware build-native TI_ROOT=/path"; \
		exit 1; \
	fi; \
	echo "build environment OK: $$(uname -s) $$(uname -m), TI_ROOT=$(TI_ROOT)"

build-native: check-tools
	$(BUILD_ENV) $(MAKE) -C "$(FIRMWARE_DIR)" cleanApp
	$(BUILD_ENV) $(MAKE) -C "$(FIRMWARE_DIR)" bin L3_VARIANT_DEFS="--define=N_TX=3 --define=ENABLE_HWA_SMOKE=1 --define=SNAPSHOT_DUMP=1 --define=HWA_CHAINED_SNAPSHOT_RING=1 --define=CONFIGURABLE_CAPTURE=1 --define=HYBRID_CADENCE_CAPTURE=1 --define=L3_RING_IQ8=1 --define=L3_IQ8_EDMA_PACK=1"
	mkdir -p "$(RELEASE_DIR)"
	cp "$(FIRMWARE_DIR)/l3_dump.bin" "$(RELEASE_DIR)/$(RELEASE_NAME)"
	sha256sum "$(RELEASE_DIR)/$(RELEASE_NAME)"

# --- Containerised build (works from macOS / arm64) --------------------------
#
# TI's tools are Linux x86_64 with 32-bit installer stubs, so the host cannot
# be an Apple Silicon Mac or a Pi. These targets run the SAME build-native
# recipe inside an emulated x86_64 container, with the repo bind-mounted so
# firmware/releases/ lands back on the host.

# Downloads the installers TI serves without a login. The other two are
# login- and export-control-gated; this names them and stops rather than
# leaving a half-populated directory that fails later inside docker build.
#
# Every download is magic-byte checked: TI answers a gated URL with HTTP 200
# and an HTML consent page, so "it downloaded" is not evidence of success.
fetch-installers:
	@set -eu; \
	mkdir -p "$(INSTALLER_DIR)"; \
	for entry in $(FETCHABLE_INSTALLERS); do \
		name="$${entry%%|*}"; url="$${entry#*|}"; \
		if [ -f "$(INSTALLER_DIR)/$$name" ]; then echo "have:     $$name"; continue; fi; \
		echo "fetching: $$name"; \
		if ! curl -fL --progress-bar -o "$(INSTALLER_DIR)/$$name.part" "$$url"; then \
			rm -f "$(INSTALLER_DIR)/$$name.part"; \
			echo "ERROR: download failed: $$url"; exit 1; \
		fi; \
		magic=$$(od -An -tx1 -N4 "$(INSTALLER_DIR)/$$name.part" | tr -d ' \n'); \
		case "$$magic" in \
			7f454c46|504b0304) \
				mv "$(INSTALLER_DIR)/$$name.part" "$(INSTALLER_DIR)/$$name"; \
				echo "ok:       $$name ($$(wc -c <"$(INSTALLER_DIR)/$$name" | tr -d ' ') bytes)";; \
			*) \
				rm -f "$(INSTALLER_DIR)/$$name.part"; \
				echo "ERROR: $$name came back as $$magic, not ELF or ZIP."; \
				echo "       TI served a login or export-control page. Download it"; \
				echo "       manually; see firmware/README.md."; exit 1;; \
		esac; \
	done; \
	missing=0; \
	for entry in $(MANUAL_INSTALLERS); do \
		name="$${entry%%|*}"; url="$${entry#*|}"; \
		if [ -f "$(INSTALLER_DIR)/$$name" ]; then echo "have:     $$name"; else \
			missing=1; \
			echo ""; \
			echo "MANUAL:   $$name"; \
			echo "          $$url"; \
			echo "          Needs a TI login and an export-control acceptance."; \
			echo "          Save it to firmware/$(INSTALLER_DIR)/"; \
		fi; \
	done; \
	if [ "$$missing" != "0" ]; then \
		echo ""; \
		echo "Fetch the file(s) above, then re-run: make -C firmware docker-image"; \
		exit 1; \
	fi; \
	echo ""; \
	echo "All installers present. Next: make -C firmware docker-image"

docker-image: check-installers
	docker build --platform linux/amd64 -t "$(DOCKER_IMAGE)" .

docker-build:
	@if ! docker image inspect "$(DOCKER_IMAGE)" >/dev/null 2>&1; then \
		echo "ERROR: container image '$(DOCKER_IMAGE)' does not exist."; \
		echo "       Build it once with: make -C firmware docker-image"; \
		echo "       That needs the five TI installers in firmware/$(INSTALLER_DIR)/"; \
		echo "       (license-gated; see firmware/README.md)."; \
		exit 1; \
	fi
	docker run --rm --platform linux/amd64 \
		-v "$(REPO_ROOT):/work" -w /work \
		"$(DOCKER_IMAGE)" make -C firmware build-native RELEASE_NAME="$(RELEASE_NAME)"


docker-shell:
	docker run --rm -it --platform linux/amd64 \
		-v "$(REPO_ROOT):/work" -w /work \
		"$(DOCKER_IMAGE)" bash

clean:
	$(BUILD_ENV) $(MAKE) -C "$(FIRMWARE_DIR)" cleanApp
