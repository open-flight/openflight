/* Stage-1c: L3 raw-ADC rolling buffer + dump-on-command  (IWR6843 MSS/R4F).
 *
 * MILESTONE 3 (this build): REAL raw-ADC capture via HARDWARE-triggered EDMA.
 * The DFE's chirp-available hardware event (EDMA_TPCC0_REQ_DFE_CHIRP_AVAIL)
 * directly triggers an EDMA that copies one chirp (CHIRP_BYTES) from the ADCBUF
 * into the L3 rolling buffer -- the CPU is out of the per-chirp timing path, so
 * there is no read-vs-write race (the earlier software-triggered version lost
 * ~23% of chirps to that race). One self-linked SYNC_A param set fills the whole
 * ring continuously (dest auto-advances per chirp, wraps every RING_CHIRPS).
 * `l3dump` disables the channel (freeze), streams the ring, re-arms.
 *
 * HWA_CHAINED_SNAPSHOT_RING replaces that raw path with TI's production data
 * flow: the DFE triggers range FFTs directly in HWA and HWA param completion
 * triggers EDMA copies of selected range bins into the compact L3 ring. The CPU
 * rearms one frame at a time during inter-frame idle time. Completed frames
 * advance through the compact L3 ring so a trigger can preserve deterministic
 * pre-impact history plus a fixed post-impact tail.
 *
 * Chirp order filled == chirp order fired == TDM (chirp c -> tx=c%N_TX,
 * loop=c/N_TX), matching iwr6843_l3dump.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* BIOS/XDC */
#include <xdc/std.h>
#include <ti/sysbios/BIOS.h>
#include <ti/sysbios/hal/Hwi.h>
#include <ti/sysbios/knl/Semaphore.h>
#include <ti/sysbios/knl/Task.h>

/* mmWave SDK drivers + control */
#include <ti/common/sys_common.h>
#include <ti/drivers/soc/soc.h>
#include <ti/drivers/esm/esm.h>
#include <ti/drivers/crc/crc.h>
#include <ti/drivers/uart/UART.h>
#include <ti/drivers/uart/include/uartsci.h>
#include <ti/drivers/pinmux/pinmux.h>
#include <ti/drivers/mailbox/mailbox.h>
#include <ti/drivers/adcbuf/ADCBuf.h>
#include <ti/drivers/edma/edma.h>
#include <ti/drivers/hwa/hwa.h>
#include <ti/control/mmwavelink/mmwavelink.h>
#include <ti/control/mmwave/mmwave.h>
#include <ti/utils/cli/cli.h>

#include "dump_format.h"

#if defined(L3_DUMP_IQ8) || defined(L3_RING_IQ8)
#define L3_ANY_IQ8 1
#endif

/* --- task priorities (mirror the mmw demo): ctrl > CLI. -------------------- */
#define L3_INIT_TASK_PRIORITY  2
#define L3_CLI_TASK_PRIORITY   3
#define L3_HWA_REARM_TASK_PRIORITY (L3_CLI_TASK_PRIORITY - 1U)
/* Keep the live snapshot worker below CLI. SYS/BIOS Task_yield does not allow
 * lower-priority tasks to run, and a priority-4 snapshot loop starved l3dump
 * so the host only saw the echoed 7-byte "l3dump\n" command. */
#define L3_SNAPSHOT_TASK_PRIORITY 1
#define L3_CTRL_TASK_PRIORITY  5

/* ASCII CAN is reserved as an out-of-band dump cancellation byte. The CLI
 * task is executing l3dump synchronously, so RX interrupts are disabled and
 * the byte remains in the SCI register until checked between frame writes. */
#define L3_DUMP_CANCEL_BYTE 0x18U
#define L3_DUMP_CANCEL_ACK  ((uint8_t *)"ILDCANCEL")
#define L3_DUMP_CANCEL_ACK_BYTES 8U

/* --- capture geometry: MUST match the .cfg the host sends ------------------
 * N_TX / N_SAMPLES / SAVE_SAMPLES / SAVE_OFFSET_SAMPLES / LOOPS /
 * RING_FRAMES are overridable from the make line
 * (variant builds: B = N_TX=2 LOOPS=16 RING_FRAMES=12, TX2 = N_TX=3
 * LOOPS=10 RING_FRAMES=12). sensorStart REJECTS a cfg whose acquired
 * sample/loop/TX geometry mismatches. Keep chirpCfg/frameCfg in sync with
 * N_TX. SAVE_* only changes what raw ADC samples are copied into L3. */
#ifndef N_TX
#define N_TX              2
#endif
#define N_RX              4
#ifndef N_SAMPLES
#define N_SAMPLES         128
#endif
#ifndef SAVE_SAMPLES
#define SAVE_SAMPLES      N_SAMPLES
#endif
#ifndef SAVE_OFFSET_SAMPLES
#define SAVE_OFFSET_SAMPLES 0
#endif
#if SAVE_SAMPLES > N_SAMPLES
#error "SAVE_SAMPLES must be <= N_SAMPLES"
#endif
#if (SAVE_OFFSET_SAMPLES + SAVE_SAMPLES) > N_SAMPLES
#error "SAVE_OFFSET_SAMPLES + SAVE_SAMPLES must be <= N_SAMPLES"
#endif
#ifndef LOOPS
#define LOOPS             32
#endif
#ifndef SNAPSHOT_BINS
#define SNAPSHOT_BINS     16
#endif
#ifndef SNAPSHOT_BIN_START
#define SNAPSHOT_BIN_START 2
#endif
#if SNAPSHOT_BINS > N_SAMPLES
#error "SNAPSHOT_BINS must be <= N_SAMPLES"
#endif
#if (SNAPSHOT_BIN_START + SNAPSHOT_BINS) > N_SAMPLES
#error "SNAPSHOT_BIN_START + SNAPSHOT_BINS must be <= N_SAMPLES"
#endif
#ifdef SNAPSHOT_DYNAMIC_WINDOWS
#ifndef HWA_CHAINED_SNAPSHOT_RING
#error "SNAPSHOT_DYNAMIC_WINDOWS requires HWA_CHAINED_SNAPSHOT_RING"
#endif
#ifndef SNAPSHOT_MIDDLE_BIN_START
#define SNAPSHOT_MIDDLE_BIN_START 32
#endif
#ifndef SNAPSHOT_LATE_BIN_START
#define SNAPSHOT_LATE_BIN_START 47
#endif
#if (SNAPSHOT_MIDDLE_BIN_START + SNAPSHOT_BINS) > N_SAMPLES
#error "middle snapshot window exceeds the range FFT"
#endif
#if (SNAPSHOT_LATE_BIN_START + SNAPSHOT_BINS) > N_SAMPLES
#error "late snapshot window exceeds the range FFT"
#endif
#endif
#ifdef LIVE_SNAPSHOT_RING
#ifndef SNAPSHOT_DUMP
#error "LIVE_SNAPSHOT_RING requires SNAPSHOT_DUMP"
#endif
#ifndef ENABLE_HWA_SMOKE
#error "LIVE_SNAPSHOT_RING requires ENABLE_HWA_SMOKE"
#endif
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
#ifndef SNAPSHOT_DUMP
#error "HWA_CHAINED_SNAPSHOT_RING requires SNAPSHOT_DUMP"
#endif
#ifndef ENABLE_HWA_SMOKE
#error "HWA_CHAINED_SNAPSHOT_RING requires ENABLE_HWA_SMOKE"
#endif
#ifdef LIVE_SNAPSHOT_RING
#error "Select either LIVE_SNAPSHOT_RING or HWA_CHAINED_SNAPSHOT_RING"
#endif
#endif
#define CHIRPS_PER_FRAME  (N_TX * LOOPS)                        /* 64 */
#define FRAME_COMPLEX     (CHIRPS_PER_FRAME * N_RX * N_SAMPLES)   /* 32768 */
#define FRAME_BYTES       (FRAME_COMPLEX * 2 * (uint32_t)sizeof(int16_t)) /* 128 KB */
#define SAVED_FRAME_COMPLEX (CHIRPS_PER_FRAME * N_RX * SAVE_SAMPLES)
#define SAVED_FRAME_BYTES (SAVED_FRAME_COMPLEX * 2 * (uint32_t)sizeof(int16_t))
#define SNAPSHOT_CHIRP_COMPLEX (N_RX * SNAPSHOT_BINS)
#define SNAPSHOT_CHIRP_BYTES (SNAPSHOT_CHIRP_COMPLEX * 2 * (uint32_t)sizeof(int16_t))
#define SNAPSHOT_FRAME_COMPLEX (CHIRPS_PER_FRAME * SNAPSHOT_CHIRP_COMPLEX)
#define SNAPSHOT_FRAME_BYTES (SNAPSHOT_FRAME_COMPLEX * 2 * (uint32_t)sizeof(int16_t))
/* One chirp in ADCBUF (non-interleaved): N_RX x N_SAMPLES x 4 bytes. */
#define CHIRP_BYTES       (N_RX * N_SAMPLES * 2 * (uint32_t)sizeof(int16_t))  /* 2048 */
#define SAVED_CHIRP_BYTES (N_RX * SAVE_SAMPLES * 2 * (uint32_t)sizeof(int16_t))

/* --- rolling buffer (default L3 = 6 banks x 128 KB = 768 KB) ----------------
 * The raw-ring build fills L3 exactly. LIVE_SNAPSHOT_RING is the first compact
 * prototype: EDMA captures one raw frame into scratch, then the HWA compresses
 * selected FFT range bins into the rolling ring. */
#ifndef RING_FRAMES
#define RING_FRAMES  6
#endif
#ifndef HWA_POST_TRIGGER_FRAMES
#define HWA_POST_TRIGGER_FRAMES 8U
#endif
#if defined(HWA_CHAINED_SNAPSHOT_RING) && !defined(CONFIGURABLE_CAPTURE)
#if HWA_POST_TRIGGER_FRAMES >= RING_FRAMES
#error "HWA_POST_TRIGGER_FRAMES must leave at least one pre-trigger frame"
#endif
#endif
#define RING_CHIRPS  (RING_FRAMES * CHIRPS_PER_FRAME)
#if defined(LIVE_SNAPSHOT_RING) || defined(HWA_CHAINED_SNAPSHOT_RING)
#define RING_FRAME_COMPLEX SNAPSHOT_FRAME_COMPLEX
#else
#define RING_FRAME_COMPLEX SAVED_FRAME_COMPLEX
#endif

#ifdef CONFIGURABLE_CAPTURE
#ifndef HWA_CHAINED_SNAPSHOT_RING
#error "CONFIGURABLE_CAPTURE requires HWA_CHAINED_SNAPSHOT_RING"
#endif
#define L3_TOTAL_BYTES         (6U * 128U * 1024U)
#define L3_MAX_CAPTURE_FRAMES  64U
#define L3_MAX_LOOPS           16U
#define L3_MIN_LOOPS           2U
#ifdef L3_RING_IQ8
/* The HWA emits complex16 samples. One maximum-size production frame lands in
 * scratch, then the rearm task block-quantizes it into the compact IQ8 ring.
 * IQ16 mode uses the whole arena directly; IQ8 mode overlays ping-pong scratch
 * onto the upper portion that its compressed capture does not use. */
#define L3_IQ8_HWA_SHIFT      4U
#define L3_IQ8_HWA_SCALE      (1U << L3_IQ8_HWA_SHIFT)
#ifdef L3_IQ8_SPARSE_SCALE
/* Preview every eighth complex sample to select a per-frame IQ8 scale before
 * the full packing pass. This keeps the HWA rearm path inside the 2 ms budget. */
#define L3_IQ8_SCALE_COMPLEX_STRIDE 8U
#endif
#define L3_RING_MAX_BINS       64U
#define L3_CAPTURE_FORMAT_IQ16 0U
#define L3_CAPTURE_FORMAT_IQ8  1U
#define L3_IQ16_SCRATCH_FRAME_BYTES  \
    (N_TX * L3_MAX_LOOPS * N_RX * L3_RING_MAX_BINS * 2U * \
     (uint32_t)sizeof(int16_t))
#define L3_IQ16_SCRATCH_BYTES  (2U * L3_IQ16_SCRATCH_FRAME_BYTES)
#define L3_IQ16_SCRATCH_WORDS  \
    (L3_IQ16_SCRATCH_FRAME_BYTES / (uint32_t)sizeof(int16_t))
#define L3_IQ8_CAPTURE_BYTES   (L3_TOTAL_BYTES - L3_IQ16_SCRATCH_BYTES)
#endif
#define L3_DEFAULT_PRE_START   20U
#define L3_DEFAULT_PRE_BINS    32U
#define L3_DEFAULT_POST_START  32U
#define L3_DEFAULT_LATE_START  47U
#define L3_DEFAULT_POST_BINS   53U
#define L3_DEFAULT_POST_FRAMES 16U
#ifdef HYBRID_CADENCE_CAPTURE
#define L3_DEFAULT_POST_STRIDE 2U
#else
#define L3_DEFAULT_POST_STRIDE 1U
#endif
#define L3_MAX_POST_STRIDE     16U

typedef struct {
    uint8_t preStart;
    uint8_t preBins;
    uint8_t postStart;
    uint8_t postBins;
    uint8_t lateStart;
    uint8_t postFrames;
    uint8_t postStride;
    uint8_t preFrames;
    uint8_t totalFrames;
    uint16_t loops;
    uint16_t chirpsPerFrame;
    uint32_t preFrameBytes;
    uint32_t postFrameBytes;
    uint32_t postBaseOffset;
    uint32_t usedBytes;
    uint8_t phased;
    uint8_t requestedPreFrames;
    uint8_t impactStart;
    uint8_t impactBins;
    uint8_t impactFrames;
    uint8_t ballFrames;
    uint32_t impactFrameBytes;
} l3_capture_plan_t;

#pragma DATA_SECTION(g_ring, ".l3ring")
#pragma DATA_ALIGN(g_ring, 8)
static uint8_t g_ring[L3_TOTAL_BYTES];
#ifdef L3_RING_IQ8
static uint8_t gCaptureFormat = L3_CAPTURE_FORMAT_IQ16;
#define g_iq16FrameScratch \
    (*((int16_t (*)[2][L3_IQ16_SCRATCH_WORDS]) \
       (void *)&g_ring[L3_IQ8_CAPTURE_BYTES]))
#endif
static l3_capture_plan_t gCapturePlan = {
    L3_DEFAULT_PRE_START,
    L3_DEFAULT_PRE_BINS,
    L3_DEFAULT_POST_START,
    L3_DEFAULT_POST_BINS,
    L3_DEFAULT_LATE_START,
    L3_DEFAULT_POST_FRAMES,
    L3_DEFAULT_POST_STRIDE,
    0U, 0U, 0U, 0U, 0U, 0U, 0U,
    0U, 0U, 0U, 0U, 0U, 0U, 0U
};
static uint8_t gFrameBinStart[L3_MAX_CAPTURE_FRAMES];
static uint8_t gFrameBinCount[L3_MAX_CAPTURE_FRAMES];
static uint16_t gFrameDeltaUs[L3_MAX_CAPTURE_FRAMES];
static uint32_t gFrameOffset[L3_MAX_CAPTURE_FRAMES];
static uint32_t gFrameBytes[L3_MAX_CAPTURE_FRAMES];
#ifdef L3_ANY_IQ8
static uint16_t gFrameIq8Scale[L3_MAX_CAPTURE_FRAMES];
#endif
#else
#pragma DATA_SECTION(g_ring, ".l3ring")
#pragma DATA_ALIGN(g_ring, 8)
static int16_t g_ring[RING_FRAMES][RING_FRAME_COMPLEX * 2];

#ifdef SNAPSHOT_DYNAMIC_WINDOWS
/* Stored in ring-slot order and emitted directly before the v4 IQ payload. */
static uint8_t gFrameBinStart[RING_FRAMES];
#endif
#endif

#ifdef LIVE_SNAPSHOT_RING
/* Two raw frame scratch buffers let EDMA keep chirping while the snapshot task
 * compresses the previously completed frame into the compact ring. */
#pragma DATA_SECTION(g_rawFrame, ".l3scratch")
#pragma DATA_ALIGN(g_rawFrame, 8)
static int16_t g_rawFrame[2][FRAME_COMPLEX * 2];
#endif

/* --- EDMA: the DFE chirp-available hardware event channel + shadow link ----- */
#define L3_EDMA_CHANNEL       EDMA_TPCC0_REQ_DFE_CHIRP_AVAIL
#define L3_EDMA_LINK_CHANNEL  EDMA_NUM_DMA_CHANNELS
#define L3_EDMA_LINK_CHANNEL_PONG (EDMA_NUM_DMA_CHANNELS + 1U)
#ifdef HWA_CHAINED_SNAPSHOT_RING
/* Match TI RangeProc's reserved request lines. HWA param-done events drive the
 * ping/pong output channels; their completion chains a two-hot signature into
 * the dummy HWA paramsets so the next DFE-triggered FFT can run. */
#define L3_HWA_OUT_PING_CHANNEL EDMA_TPCC0_REQ_HWACC_0
#define L3_HWA_OUT_PONG_CHANNEL EDMA_TPCC0_REQ_HWACC_1
#define L3_HWA_SIGNATURE_CHANNEL EDMA_TPCC0_REQ_FREE_7
#ifdef L3_IQ8_EDMA_PACK
#define L3_IQ8_PACK_PING_CHANNEL EDMA_TPCC0_REQ_FREE_8
#define L3_IQ8_PACK_PONG_CHANNEL EDMA_TPCC0_REQ_FREE_9
#endif
#define L3_HWA_OUT_PING_SHADOW EDMA_NUM_DMA_CHANNELS
#define L3_HWA_OUT_PONG_SHADOW (EDMA_NUM_DMA_CHANNELS + 1U)
#define L3_HWA_SIGNATURE_SHADOW (EDMA_NUM_DMA_CHANNELS + 2U)
#define L3_HWA_PARAM_DUMMY_PING 0U
#define L3_HWA_PARAM_FFT_PING   1U
#define L3_HWA_PARAM_DUMMY_PONG 2U
#define L3_HWA_PARAM_FFT_PONG   3U
#endif

/* --- SDK handles ----------------------------------------------------------- */
static SOC_Handle    gSocHandle;
static UART_Handle   gCliUart;    /* MSS UARTA, instance 0, 115200, has RX */
static UART_Handle   gDataUart;   /* MSS UARTB, instance 1, 921600, TX only */
static MMWave_Handle gMMWaveHandle;
static EDMA_Handle   gEdmaHandle;
static ADCBuf_Handle gAdcbufHandle;
#ifdef ENABLE_HWA_SMOKE
static HWA_Handle    gHwaHandle;
static int32_t       gHwaOpenErr;
static uint8_t       gHwaOpened;
static volatile uint8_t gHwaDone;
static int32_t       gHwaTestErr;
static uint16_t      gHwaTestPeakBin;
static uint32_t      gHwaTestPeakPower;
static uint32_t      gHwaTestRuns;
static int32_t       gHwaRealErr;
static uint16_t      gHwaRealPeakBin;
static uint32_t      gHwaRealPeakPower;
static uint32_t      gHwaRealRuns;
#ifdef LIVE_SNAPSHOT_RING
static uint8_t       gHwaFftConfigured;
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
static Semaphore_Handle gHwaRearmSemaphore;
static Semaphore_Handle gHwaFreezeSemaphore;
#ifdef L3_IQ8_EDMA_PACK
static Semaphore_Handle gIq8EdmaDoneSemaphore;
#endif
#endif
#endif
static uint8_t       gSensorOpened;
static uint32_t      gCpuClock = 200U * 1000000U;

/* --- capture state / diagnostics ------------------------------------------- */
static volatile uint8_t  gCaptureActive;
static uint16_t gFramePeriodUs;         /* from the accepted frameCfg -> header */
static volatile uint32_t gNumFrame;     /* frame-start ISR count (liveness) */
static volatile uint32_t gRingFrame;    /* completed snapshot frames since the
                                         * last dump; next write/oldest slot is
                                         * gRingFrame % RING_FRAMES once full */
static volatile uint32_t gNumWrap;      /* EDMA ring-wrap count */
static volatile uint32_t gCalibStatus;  /* RL_RF_AE_INITCALIBSTATUS payload */
static volatile uint32_t gRfFaults;     /* CPU/ESM/analog fault events */
#ifdef LIVE_SNAPSHOT_RING
static volatile uint32_t gRawFrameReadyMask;
static volatile uint32_t gRawFrameDrops;
static volatile uint32_t gSnapshotFrames;
static volatile uint32_t gSnapshotErrors;
static volatile uint8_t  gSnapshotBusy;
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
static volatile uint32_t gHwaFrameDone;
static volatile uint32_t gHwaOutputDone;
static volatile uint32_t gHwaRearms;
static volatile uint32_t gHwaRearmErrors;
static volatile uint32_t gHwaMissedFrameStarts;
static volatile uint8_t  gHwaArmedForFrame;
static volatile uint8_t  gHwaDoneSeen;
static volatile uint8_t  gHwaOutputSeen;
static volatile uint8_t  gHwaRearmPending;
static volatile uint8_t  gHwaRearmBusy;
static volatile uint8_t  gHwaFreezeRequested;
static volatile uint8_t  gHwaShutdownRequested;
static volatile uint32_t gHwaFreezeRequestFrame;
static volatile uint32_t gHwaFreezeTargetFrame;
static volatile uint32_t gHwaFreezeRequests;
static volatile uint32_t gHwaFreezeCompletions;
static volatile uint32_t gHwaFreezeTimeouts;
static volatile uint32_t gHwaFreezeRestarts;
#ifdef CONFIGURABLE_CAPTURE
static volatile uint32_t gPreFramesCaptured;
static volatile uint32_t gPostFramesCaptured;
static volatile uint32_t gPostFramesObserved;
static volatile uint8_t  gPostCaptureStarted;
static volatile uint8_t  gActiveFrameIsPost;
static volatile uint8_t  gActiveFrameShouldKeep;
#ifdef L3_RING_IQ8
static volatile uint8_t  gIq8Pending;
static volatile uint32_t gIq8PendingSlot;
static volatile uint8_t  gIq8PendingScratch;
static volatile uint8_t  gIq8ActiveScratch;
static volatile uint32_t gIq8PackFrames;
static volatile uint32_t gIq8PackOverruns;
static volatile uint32_t gIq8ClippedComponents;
#ifdef L3_IQ8_EDMA_PACK
static volatile uint8_t  gIq8EdmaBusy[2];
static volatile uint32_t gIq8EdmaDone;
static volatile uint32_t gIq8EdmaErrors;
static volatile uint32_t gIq8EdmaWaits;
static uint16_t gIq8FixedScale = 128U;
static uint8_t  gIq8FixedShift = 7U;
#endif
#endif
#endif
#endif

/* Config pulled from the CLI mmWave extension (kept off the stack -- large). */
static MMWave_OpenCfg gOpenCfg;
static MMWave_CtrlCfg gCtrlCfg;

/* Forward declarations. */
int32_t l3_cli_dump(int32_t argc, char *argv[]);
static int32_t l3_cli_sensorStart(int32_t argc, char *argv[]);
static int32_t l3_cli_sensorStop(int32_t argc, char *argv[]);
static int32_t l3_cli_stats(int32_t argc, char *argv[]);
#ifdef CONFIGURABLE_CAPTURE
static int32_t l3_cli_captureCfg(int32_t argc, char *argv[]);
static int32_t l3_cli_phaseCaptureCfg(int32_t argc, char *argv[]);
#ifdef L3_RING_IQ8
static int32_t l3_cli_captureFormat(int32_t argc, char *argv[]);
#ifdef L3_IQ8_EDMA_PACK
static int32_t l3_cli_iq8Scale(int32_t argc, char *argv[]);
#endif
#endif
#endif
#ifdef ENABLE_HWA_SMOKE
static int32_t l3_cli_hwaStats(int32_t argc, char *argv[]);
static int32_t l3_cli_hwaTest(int32_t argc, char *argv[]);
static int32_t l3_cli_hwaReal(int32_t argc, char *argv[]);
#endif
static int32_t l3_armCapture(void);
#ifdef LIVE_SNAPSHOT_RING
static void l3_snapshotTask(UArg arg0, UArg arg1);
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
static void l3_hwaRearmTask(UArg arg0, UArg arg1);
#endif

#ifdef CONFIGURABLE_CAPTURE
static int32_t l3_parseU8(const char *text, uint8_t *value)
{
    char *end = NULL;
    long parsed = strtol(text, &end, 10);

    if (text == end || end == NULL || *end != '\0' || parsed < 0L || parsed > 255L) {
        return -1;
    }
    *value = (uint8_t)parsed;
    return 0;
}

static uint8_t l3_captureUsesIq8(void)
{
#ifdef L3_RING_IQ8
    return gCaptureFormat == L3_CAPTURE_FORMAT_IQ8;
#else
    return 0U;
#endif
}

static uint32_t l3_captureCapacityBytes(void)
{
#ifdef L3_RING_IQ8
    if (l3_captureUsesIq8()) {
        return L3_IQ8_CAPTURE_BYTES;
    }
#endif
    return L3_TOTAL_BYTES;
}

static uint32_t l3_captureBytesPerComplex(void)
{
    return l3_captureUsesIq8()
               ? 2U : 2U * (uint32_t)sizeof(int16_t);
}

#ifdef L3_RING_IQ8
static int32_t l3_cli_captureFormat(int32_t argc, char *argv[])
{
    if (gCaptureActive) {
        CLI_write("Error: stop the sensor before captureFormat\n");
        return -1;
    }
    if (argc != 2) {
        CLI_write("Error: captureFormat needs iq16 or iq8\n");
        return -1;
    }
    if (strcmp(argv[1], "iq16") == 0) {
        gCaptureFormat = L3_CAPTURE_FORMAT_IQ16;
    } else if (strcmp(argv[1], "iq8") == 0) {
        gCaptureFormat = L3_CAPTURE_FORMAT_IQ8;
    } else {
        CLI_write("Error: captureFormat needs iq16 or iq8\n");
        return -1;
    }
    gCapturePlan.preFrames = 0U;
    gCapturePlan.totalFrames = 0U;
    gCapturePlan.usedBytes = 0U;
    CLI_write("Capture format: %s\n", l3_captureUsesIq8() ? "iq8" : "iq16");
    return 0;
}

#ifdef L3_IQ8_EDMA_PACK
static int32_t l3_cli_iq8Scale(int32_t argc, char *argv[])
{
    char *end = NULL;
    long scale;
    uint8_t shift = 0U;
    uint32_t value;

    if (gCaptureActive) {
        CLI_write("Error: stop the sensor before iq8Scale\n");
        return -1;
    }
    if (argc != 2) {
        CLI_write("Error: iq8Scale needs a power of two from 16 to 256\n");
        return -1;
    }
    scale = strtol(argv[1], &end, 10);
    if (argv[1] == end || end == NULL || *end != '\0' || scale < 16L ||
        scale > 256L || (((uint32_t)scale & ((uint32_t)scale - 1U)) != 0U)) {
        CLI_write("Error: iq8Scale needs a power of two from 16 to 256\n");
        return -1;
    }
    value = (uint32_t)scale;
    while (value > 1U) {
        value >>= 1U;
        shift++;
    }
    gIq8FixedScale = (uint16_t)scale;
    gIq8FixedShift = shift;
    CLI_write("IQ8 fixed scale: %u (HWA shift %u)\n",
              (unsigned)gIq8FixedScale, (unsigned)gIq8FixedShift);
    return 0;
}
#endif
#endif

static int32_t l3_finalizeCapturePlan(uint16_t loops)
{
    uint32_t bytesPerBin;
    uint32_t bytesPerComplex;
    uint32_t captureBytes;
    uint32_t postBytes;
    uint32_t remaining;
    uint32_t preFrames;
    uint32_t frame;
    uint32_t cursor;

    if (loops < L3_MIN_LOOPS || loops > L3_MAX_LOOPS || (loops & 1U) != 0U) {
        CLI_write("Error: loops must be even and between %u and %u\n",
                  (unsigned)L3_MIN_LOOPS, (unsigned)L3_MAX_LOOPS);
        return -1;
    }
#ifdef L3_RING_IQ8
    if (l3_captureUsesIq8() &&
        (gCapturePlan.preBins > L3_RING_MAX_BINS ||
         gCapturePlan.postBins > L3_RING_MAX_BINS ||
         (gCapturePlan.phased &&
          gCapturePlan.impactBins > L3_RING_MAX_BINS))) {
        CLI_write("Error: iq8 capture windows cannot exceed %u bins\n",
                  (unsigned)L3_RING_MAX_BINS);
        return -1;
    }
#endif
    if (gCapturePlan.preBins == 0U || gCapturePlan.postBins == 0U ||
        gCapturePlan.postFrames == 0U ||
        gCapturePlan.postStride == 0U ||
        gCapturePlan.postStride > L3_MAX_POST_STRIDE ||
        gCapturePlan.postFrames >= L3_MAX_CAPTURE_FRAMES ||
        gFramePeriodUs == 0U ||
        ((uint32_t)gFramePeriodUs * gCapturePlan.postStride) > 0xFFFFU ||
        ((uint32_t)gCapturePlan.preStart + gCapturePlan.preBins) > N_SAMPLES ||
        ((uint32_t)gCapturePlan.postStart + gCapturePlan.postBins) > N_SAMPLES ||
        ((uint32_t)gCapturePlan.lateStart + gCapturePlan.postBins) > N_SAMPLES ||
        (gCapturePlan.phased &&
         (gCapturePlan.requestedPreFrames == 0U ||
          gCapturePlan.impactBins == 0U ||
          gCapturePlan.impactFrames == 0U ||
          gCapturePlan.ballFrames == 0U ||
          ((uint32_t)gCapturePlan.impactStart + gCapturePlan.impactBins) > N_SAMPLES ||
          ((uint32_t)gCapturePlan.requestedPreFrames +
           gCapturePlan.postFrames) > L3_MAX_CAPTURE_FRAMES))) {
        CLI_write("Error: captureCfg needs valid windows and 1-%u post frames\n",
                  (unsigned)(L3_MAX_CAPTURE_FRAMES - 1U));
        return -1;
    }

    gCapturePlan.loops = loops;
    gCapturePlan.chirpsPerFrame = (uint16_t)(N_TX * loops);
    bytesPerComplex = l3_captureBytesPerComplex();
    captureBytes = l3_captureCapacityBytes();
    bytesPerBin = (uint32_t)gCapturePlan.chirpsPerFrame *
                  N_RX * bytesPerComplex;
    gCapturePlan.preFrameBytes = bytesPerBin * gCapturePlan.preBins;
    gCapturePlan.postFrameBytes = bytesPerBin * gCapturePlan.postBins;
    gCapturePlan.impactFrameBytes = bytesPerBin * gCapturePlan.impactBins;
    postBytes = gCapturePlan.phased
                    ? (gCapturePlan.impactFrameBytes * gCapturePlan.impactFrames) +
                      (gCapturePlan.postFrameBytes * gCapturePlan.ballFrames)
                    : gCapturePlan.postFrameBytes * gCapturePlan.postFrames;
    if (postBytes >= captureBytes) {
        CLI_write("Error: post-trigger capture needs %u bytes; L3 has %u\n",
                  (unsigned)postBytes, (unsigned)captureBytes);
        return -1;
    }
    remaining = captureBytes - postBytes;
    preFrames = gCapturePlan.phased
                    ? gCapturePlan.requestedPreFrames
                    : remaining / gCapturePlan.preFrameBytes;
    if (preFrames == 0U) {
        CLI_write("Error: capture plan leaves no pre-trigger frame\n");
        return -1;
    }
    if (!gCapturePlan.phased &&
        preFrames + gCapturePlan.postFrames > L3_MAX_CAPTURE_FRAMES) {
        preFrames = L3_MAX_CAPTURE_FRAMES - gCapturePlan.postFrames;
    }
    if (preFrames == 0U ||
        preFrames * gCapturePlan.preFrameBytes > remaining) {
        CLI_write("Error: capture plan exceeds L3 after pre-trigger reservation\n");
        return -1;
    }

    gCapturePlan.preFrames = (uint8_t)preFrames;
    gCapturePlan.totalFrames =
        (uint8_t)(preFrames + gCapturePlan.postFrames);
    gCapturePlan.postBaseOffset = preFrames * gCapturePlan.preFrameBytes;
    cursor = 0U;

    for (frame = 0U; frame < preFrames; frame++) {
        gFrameOffset[frame] = cursor;
        gFrameBinStart[frame] = gCapturePlan.preStart;
        gFrameBinCount[frame] = gCapturePlan.preBins;
        gFrameDeltaUs[frame] = gFramePeriodUs;
        gFrameBytes[frame] = gCapturePlan.preFrameBytes;
        cursor += gCapturePlan.preFrameBytes;
    }

    if (gCapturePlan.phased) {
        for (frame = 0U; frame < gCapturePlan.impactFrames; frame++) {
            uint32_t slot = preFrames + frame;
            gFrameOffset[slot] = cursor;
            gFrameBinStart[slot] = gCapturePlan.impactStart;
            gFrameBinCount[slot] = gCapturePlan.impactBins;
            gFrameDeltaUs[slot] = gFramePeriodUs;
            gFrameBytes[slot] = gCapturePlan.impactFrameBytes;
            cursor += gCapturePlan.impactFrameBytes;
        }
        for (frame = 0U; frame < gCapturePlan.ballFrames; frame++) {
            uint32_t slot = preFrames + gCapturePlan.impactFrames + frame;
            gFrameOffset[slot] = cursor;
            gFrameBinStart[slot] =
                (frame < (gCapturePlan.ballFrames / 2U))
                    ? gCapturePlan.postStart : gCapturePlan.lateStart;
            gFrameBinCount[slot] = gCapturePlan.postBins;
            gFrameDeltaUs[slot] =
                (frame == 0U)
                    ? gFramePeriodUs
                    : (uint16_t)(gFramePeriodUs * gCapturePlan.postStride);
            gFrameBytes[slot] = gCapturePlan.postFrameBytes;
            cursor += gCapturePlan.postFrameBytes;
        }
    } else {
        for (frame = 0U; frame < gCapturePlan.postFrames; frame++) {
            uint32_t slot = preFrames + frame;
            gFrameOffset[slot] = cursor;
            gFrameBinStart[slot] =
                (frame < (gCapturePlan.postFrames / 2U))
                    ? gCapturePlan.postStart : gCapturePlan.lateStart;
            gFrameBinCount[slot] = gCapturePlan.postBins;
            gFrameDeltaUs[slot] =
                (frame == 0U)
                    ? gFramePeriodUs
                    : (uint16_t)(gFramePeriodUs * gCapturePlan.postStride);
            gFrameBytes[slot] = gCapturePlan.postFrameBytes;
            cursor += gCapturePlan.postFrameBytes;
        }
    }
    gCapturePlan.usedBytes = cursor;

    if (gCapturePlan.phased) {
        CLI_write("Capture plan: loops=%u pre=%ux%u@%uus impact=%ux%u@%uus "
                  "ball=%ux%u@%uus frames=%u bytes=%u/%u starts=%u/%u/%u/%u "
                  "stride=%u\n",
                  (unsigned)gCapturePlan.loops,
                  (unsigned)gCapturePlan.preFrames,
                  (unsigned)gCapturePlan.preBins,
                  (unsigned)gFramePeriodUs,
                  (unsigned)gCapturePlan.impactFrames,
                  (unsigned)gCapturePlan.impactBins,
                  (unsigned)gFramePeriodUs,
                  (unsigned)gCapturePlan.ballFrames,
                  (unsigned)gCapturePlan.postBins,
                  (unsigned)(gFramePeriodUs * gCapturePlan.postStride),
                  (unsigned)gCapturePlan.totalFrames,
                  (unsigned)gCapturePlan.usedBytes,
                  (unsigned)captureBytes,
                  (unsigned)gCapturePlan.preStart,
                  (unsigned)gCapturePlan.impactStart,
                  (unsigned)gCapturePlan.postStart,
                  (unsigned)gCapturePlan.lateStart,
                  (unsigned)gCapturePlan.postStride);
    } else {
        CLI_write("Capture plan: loops=%u pre=%ux%u@%uus post=%ux%u@%uus "
                  "frames=%u bytes=%u/%u starts=%u/%u/%u stride=%u\n",
                  (unsigned)gCapturePlan.loops,
                  (unsigned)gCapturePlan.preFrames,
                  (unsigned)gCapturePlan.preBins,
                  (unsigned)gFramePeriodUs,
                  (unsigned)gCapturePlan.postFrames,
                  (unsigned)gCapturePlan.postBins,
                  (unsigned)(gFramePeriodUs * gCapturePlan.postStride),
                  (unsigned)gCapturePlan.totalFrames,
                  (unsigned)gCapturePlan.usedBytes,
                  (unsigned)captureBytes,
                  (unsigned)gCapturePlan.preStart,
                  (unsigned)gCapturePlan.postStart,
                  (unsigned)gCapturePlan.lateStart,
                  (unsigned)gCapturePlan.postStride);
    }
    return 0;
}

/* captureCfg <preStart> <preBins> <postStart> <postBins> <lateStart>
 *            <postFrames> [postStride]
 *
 * The post reservation is fixed by the requested count. All remaining L3 is
 * converted into pre-trigger ring slots after frameCfg supplies the loop count.
 */
static int32_t l3_cli_captureCfg(int32_t argc, char *argv[])
{
    uint8_t values[7];
    uint32_t valueCount;
    uint32_t i;

    if (gCaptureActive) {
        CLI_write("Error: stop the sensor before captureCfg\n");
        return -1;
    }
    if (argc != 7 && argc != 8) {
        CLI_write("Error: captureCfg needs 6 or 7 values: "
                  "preStart preBins postStart postBins lateStart postFrames "
                  "[postStride]\n");
        return -1;
    }
    valueCount = (uint32_t)argc - 1U;
    for (i = 0U; i < valueCount; i++) {
        if (l3_parseU8(argv[i + 1U], &values[i]) != 0) {
            CLI_write("Error: captureCfg values must be uint8 integers\n");
            return -1;
        }
    }
    if (valueCount == 6U) {
        values[6] = L3_DEFAULT_POST_STRIDE;
    }
    if (values[1] == 0U || values[3] == 0U || values[5] == 0U ||
        values[6] == 0U || values[6] > L3_MAX_POST_STRIDE ||
        values[5] >= L3_MAX_CAPTURE_FRAMES ||
        ((uint32_t)values[0] + values[1]) > N_SAMPLES ||
        ((uint32_t)values[2] + values[3]) > N_SAMPLES ||
        ((uint32_t)values[4] + values[3]) > N_SAMPLES) {
        CLI_write("Error: captureCfg needs valid %u-bin FFT windows and "
                  "1-%u post frames\n",
                  (unsigned)N_SAMPLES,
                  (unsigned)(L3_MAX_CAPTURE_FRAMES - 1U));
        return -1;
    }
    gCapturePlan.preStart = values[0];
    gCapturePlan.preBins = values[1];
    gCapturePlan.postStart = values[2];
    gCapturePlan.postBins = values[3];
    gCapturePlan.lateStart = values[4];
    gCapturePlan.postFrames = values[5];
    gCapturePlan.postStride = values[6];
    gCapturePlan.phased = 0U;
    gCapturePlan.requestedPreFrames = 0U;
    gCapturePlan.impactStart = 0U;
    gCapturePlan.impactBins = 0U;
    gCapturePlan.impactFrames = 0U;
    gCapturePlan.ballFrames = values[5];
    gCapturePlan.preFrames = 0U;
    gCapturePlan.totalFrames = 0U;
    gCapturePlan.usedBytes = 0U;
    return 0;
}

/* phaseCaptureCfg <preStart> <preBins> <preFrames>
 *                 <impactStart> <impactBins> <impactFrames>
 *                 <postStart> <postBins> <lateStart> <ballFrames>
 *                 <ballStride>
 *
 * The pre and impact phases retain every acquisition. The ball phase retains
 * its first acquisition, then every ballStride acquisition. Its first half
 * uses postStart and its second half uses lateStart.
 */
static int32_t l3_cli_phaseCaptureCfg(int32_t argc, char *argv[])
{
    uint8_t values[11];
    uint32_t i;
    uint32_t totalPost;

    if (gCaptureActive) {
        CLI_write("Error: stop the sensor before phaseCaptureCfg\n");
        return -1;
    }
    if (argc != 12) {
        CLI_write("Error: phaseCaptureCfg needs 11 values: preStart preBins "
                  "preFrames impactStart impactBins impactFrames postStart "
                  "postBins lateStart ballFrames ballStride\n");
        return -1;
    }
    for (i = 0U; i < 11U; i++) {
        if (l3_parseU8(argv[i + 1U], &values[i]) != 0) {
            CLI_write("Error: phaseCaptureCfg values must be uint8 integers\n");
            return -1;
        }
    }
    totalPost = (uint32_t)values[5] + values[9];
    if (values[1] == 0U || values[2] == 0U ||
        values[4] == 0U || values[5] == 0U ||
        values[7] == 0U || values[9] == 0U ||
        values[10] == 0U || values[10] > L3_MAX_POST_STRIDE ||
        totalPost >= L3_MAX_CAPTURE_FRAMES ||
        ((uint32_t)values[2] + totalPost) > L3_MAX_CAPTURE_FRAMES ||
        ((uint32_t)values[0] + values[1]) > N_SAMPLES ||
        ((uint32_t)values[3] + values[4]) > N_SAMPLES ||
        ((uint32_t)values[6] + values[7]) > N_SAMPLES ||
        ((uint32_t)values[8] + values[7]) > N_SAMPLES) {
        CLI_write("Error: phaseCaptureCfg needs valid %u-bin windows and "
                  "1-%u total frames\n",
                  (unsigned)N_SAMPLES,
                  (unsigned)(L3_MAX_CAPTURE_FRAMES - 1U));
        return -1;
    }

    gCapturePlan.preStart = values[0];
    gCapturePlan.preBins = values[1];
    gCapturePlan.requestedPreFrames = values[2];
    gCapturePlan.impactStart = values[3];
    gCapturePlan.impactBins = values[4];
    gCapturePlan.impactFrames = values[5];
    gCapturePlan.postStart = values[6];
    gCapturePlan.postBins = values[7];
    gCapturePlan.lateStart = values[8];
    gCapturePlan.ballFrames = values[9];
    gCapturePlan.postFrames = (uint8_t)totalPost;
    gCapturePlan.postStride = values[10];
    gCapturePlan.phased = 1U;
    gCapturePlan.preFrames = 0U;
    gCapturePlan.totalFrames = 0U;
    gCapturePlan.usedBytes = 0U;
    return 0;
}
#endif

#ifdef ENABLE_HWA_SMOKE
#define HWA_FFT_SAMPLES      128U
#define HWA_FFT_RX           4U
#define HWA_FFT_TONE_BIN     9U
#define HWA_COMPLEX16_BYTES  4U
#define HWA_MEM_STRIDE       (16U * 1024U)
#define HWA_TEST_MEM0        SOC_XWR68XX_MSS_HWA_MEM0_BASE_ADDRESS
#define HWA_TEST_MEM2        SOC_XWR68XX_MSS_HWA_MEM2_BASE_ADDRESS

static uint32_t l3_log2_u32(uint32_t x)
{
    uint32_t n = 0U;
    while ((1U << n) < x) {
        n++;
    }
    return n;
}

static void l3_hwaDoneCB(void *arg)
{
    (void)arg;
    gHwaDone = 1U;
}

static int32_t l3_hwaConfigFft(void)
{
    HWA_ParamConfig paramCfg;
    HWA_CommonConfig commonCfg;
    int32_t errCode;

    memset((void *)&paramCfg, 0, sizeof(paramCfg));
    paramCfg.triggerMode = HWA_TRIG_MODE_SOFTWARE;
    paramCfg.accelMode = HWA_ACCELMODE_FFT;
    paramCfg.source.srcAddr = 0U;
    paramCfg.source.srcAcnt = HWA_FFT_SAMPLES - 1U;
    paramCfg.source.srcAIdx = HWA_FFT_RX * HWA_COMPLEX16_BYTES;
    paramCfg.source.srcBcnt = HWA_FFT_RX - 1U;
    paramCfg.source.srcBIdx = HWA_COMPLEX16_BYTES;
    paramCfg.source.srcRealComplex = HWA_SAMPLES_FORMAT_COMPLEX;
    paramCfg.source.srcWidth = HWA_SAMPLES_WIDTH_16BIT;
    paramCfg.source.srcSign = HWA_SAMPLES_SIGNED;
    paramCfg.source.srcConjugate = HWA_FEATURE_BIT_DISABLE;
    paramCfg.source.srcScale = 0U;
    paramCfg.source.bpmEnable = HWA_FEATURE_BIT_DISABLE;
    paramCfg.dest.dstAddr = (uint16_t)(HWA_TEST_MEM2 - HWA_TEST_MEM0);
    paramCfg.dest.dstAcnt = HWA_FFT_SAMPLES - 1U;
    paramCfg.dest.dstAIdx = HWA_FFT_RX * HWA_COMPLEX16_BYTES;
    paramCfg.dest.dstBIdx = HWA_COMPLEX16_BYTES;
    paramCfg.dest.dstRealComplex = HWA_SAMPLES_FORMAT_COMPLEX;
    paramCfg.dest.dstWidth = HWA_SAMPLES_WIDTH_16BIT;
    paramCfg.dest.dstSign = HWA_SAMPLES_SIGNED;
    paramCfg.dest.dstConjugate = HWA_FEATURE_BIT_DISABLE;
    paramCfg.dest.dstScale = 0U;
    paramCfg.dest.dstSkipInit = 0U;
    paramCfg.accelModeArgs.fftMode.fftEn = HWA_FEATURE_BIT_ENABLE;
    paramCfg.accelModeArgs.fftMode.fftSize = (uint8_t)l3_log2_u32(HWA_FFT_SAMPLES);
    paramCfg.accelModeArgs.fftMode.butterflyScaling = 0x7FU;
    paramCfg.accelModeArgs.fftMode.interfZeroOutEn = HWA_FEATURE_BIT_DISABLE;
    paramCfg.accelModeArgs.fftMode.windowEn = HWA_FEATURE_BIT_DISABLE;
    paramCfg.accelModeArgs.fftMode.windowStart = 0U;
    paramCfg.accelModeArgs.fftMode.winSymm = HWA_FFT_WINDOW_NONSYMMETRIC;
    paramCfg.accelModeArgs.fftMode.winInterpolateMode = HWA_FFT_WINDOW_INTERPOLATE_MODE_NONE;
    paramCfg.accelModeArgs.fftMode.magLogEn = HWA_FFT_MODE_MAGNITUDE_LOG2_DISABLED;
    paramCfg.accelModeArgs.fftMode.fftOutMode = HWA_FFT_MODE_OUTPUT_DEFAULT;
    paramCfg.complexMultiply.mode = HWA_COMPLEX_MULTIPLY_MODE_DISABLE;

    errCode = HWA_configParamSet(gHwaHandle, 0U, &paramCfg, NULL);
    if (errCode != 0) {
        return errCode;
    }

    memset((void *)&commonCfg, 0, sizeof(commonCfg));
    commonCfg.configMask = HWA_COMMONCONFIG_MASK_NUMLOOPS |
                           HWA_COMMONCONFIG_MASK_PARAMSTARTIDX |
                           HWA_COMMONCONFIG_MASK_PARAMSTOPIDX |
                           HWA_COMMONCONFIG_MASK_FFT1DENABLE |
                           HWA_COMMONCONFIG_MASK_INTERFERENCETHRESHOLD;
    commonCfg.numLoops = 1U;
    commonCfg.paramStartIdx = 0U;
    commonCfg.paramStopIdx = 0U;
    commonCfg.fftConfig.fft1DEnable = HWA_FEATURE_BIT_DISABLE;
    commonCfg.fftConfig.interferenceThreshold = 0xFFFFFFU;
    errCode = HWA_configCommon(gHwaHandle, &commonCfg);
    if (errCode != 0) {
        return errCode;
    }

    return 0;
}

static int32_t l3_hwaRunFft(uint16_t *peakBin, uint32_t *peakPower)
{
    int16_t *dst = (int16_t *)HWA_TEST_MEM2;
    uint32_t i, wait;
    int32_t errCode;

    *peakBin = 0U;
    *peakPower = 0U;

    memset((void *)dst, 0, HWA_MEM_STRIDE);
#ifdef LIVE_SNAPSHOT_RING
    if (!gHwaFftConfigured) {
        errCode = l3_hwaConfigFft();
        if (errCode != 0) {
            return errCode;
        }
        gHwaFftConfigured = 1U;
    }
#else
    errCode = l3_hwaConfigFft();
    if (errCode != 0) {
        return errCode;
    }
#endif

    gHwaDone = 0U;
    errCode = HWA_enableDoneInterrupt(gHwaHandle, l3_hwaDoneCB, NULL);
    if (errCode != 0) {
        return errCode;
    }
    errCode = HWA_enable(gHwaHandle, 1U);
    if (errCode != 0) {
        (void)HWA_disableDoneInterrupt(gHwaHandle);
        return errCode;
    }
#ifndef LIVE_SNAPSHOT_RING
    errCode = HWA_reset(gHwaHandle);
    if (errCode != 0) {
        (void)HWA_enable(gHwaHandle, 0U);
        (void)HWA_disableDoneInterrupt(gHwaHandle);
        return errCode;
    }
#endif
    errCode = HWA_setSoftwareTrigger(gHwaHandle);
    if (errCode != 0) {
        (void)HWA_enable(gHwaHandle, 0U);
        (void)HWA_disableDoneInterrupt(gHwaHandle);
        return errCode;
    }
#ifdef LIVE_SNAPSHOT_RING
    /* Live snapshot compression runs in the frame-to-frame timing path. A
     * BIOS tick sleep here guarantees we miss frames, but a fully tight loop
     * can starve the mmWave/CLI tasks on SYS/BIOS. Poll in short bursts and
     * yield cooperatively so the firmware stays responsive while avoiding a
     * full millisecond-scale sleep per chirp. */
    for (wait = 0U; wait < 200000U && !gHwaDone; wait++) {
        if ((wait & 0x3FFU) == 0x3FFU) {
            Task_yield();
        }
    }
#else
    for (wait = 0U; wait < 200U && !gHwaDone; wait++) {
        Task_sleep(1);
    }
#endif
    (void)HWA_enable(gHwaHandle, 0U);
    (void)HWA_disableDoneInterrupt(gHwaHandle);
    if (!gHwaDone) {
        return -102;
    }

    for (i = 0U; i < HWA_FFT_SAMPLES; i++) {
        int32_t im = (int32_t)dst[((i * HWA_FFT_RX) * 2U) + 0U];
        int32_t re = (int32_t)dst[((i * HWA_FFT_RX) * 2U) + 1U];
        uint32_t p = (uint32_t)((im * im) + (re * re));
        if (p > *peakPower) {
            *peakPower = p;
            *peakBin = (uint16_t)i;
        }
    }
    return 0;
}

#if defined(SNAPSHOT_DUMP) && !defined(HWA_CHAINED_SNAPSHOT_RING)
static int32_t l3_snapshotChirpToBuffer(const int16_t *rawChirp, int16_t *out)
{
    int16_t *src = (int16_t *)HWA_TEST_MEM0;
    int16_t *dst = (int16_t *)HWA_TEST_MEM2;
    uint16_t peakBin;
    uint32_t peakPower;
    uint32_t sample, rx, bin, outWord;
    int32_t errCode;

    memset((void *)src, 0, HWA_MEM_STRIDE);
    for (sample = 0U; sample < HWA_FFT_SAMPLES; sample++) {
        for (rx = 0U; rx < HWA_FFT_RX; rx++) {
            uint32_t rawWord = ((rx * N_SAMPLES) + sample) * 2U;
            uint32_t hwaWord = ((sample * HWA_FFT_RX) + rx) * 2U;
            src[hwaWord + 0U] = rawChirp[rawWord + 0U];
            src[hwaWord + 1U] = rawChirp[rawWord + 1U];
        }
    }

    errCode = l3_hwaRunFft(&peakBin, &peakPower);
    if (errCode != 0) {
        return errCode;
    }

    outWord = 0U;
    for (rx = 0U; rx < N_RX; rx++) {
        for (bin = 0U; bin < SNAPSHOT_BINS; bin++) {
            uint32_t srcBin = SNAPSHOT_BIN_START + bin;
            uint32_t hwaWord = ((srcBin * HWA_FFT_RX) + rx) * 2U;
            out[outWord++] = dst[hwaWord + 0U];
            out[outWord++] = dst[hwaWord + 1U];
        }
    }
    return 0;
}

#ifndef LIVE_SNAPSHOT_RING
static int32_t l3_emitSnapshotChirp(const int16_t *rawChirp)
{
    int16_t out[N_RX * SNAPSHOT_BINS * 2U];
    int32_t errCode;

    errCode = l3_snapshotChirpToBuffer(rawChirp, out);
    if (errCode != 0) {
        return errCode;
    }
    UART_writePolling(gDataUart, (uint8_t *)out, sizeof(out));
    return 0;
}
#endif
#endif
#endif

#ifdef HWA_CHAINED_SNAPSHOT_RING
static void l3_hwaMaybeQueueRearm(void)
{
    uintptr_t key;
    uint8_t queue = 0U;
    uint8_t freeze = 0U;

    key = Hwi_disable();
    if (gCaptureActive && gHwaDoneSeen && gHwaOutputSeen && !gHwaRearmPending) {
        if (gHwaShutdownRequested) {
#if defined(CONFIGURABLE_CAPTURE) && defined(L3_RING_IQ8)
            if (l3_captureUsesIq8()) {
                /* Let the task pack the completed scratch frame before
                 * acknowledging the shutdown boundary. */
                gHwaRearmPending = 1U;
                queue = 1U;
            } else
#endif
            {
                gCaptureActive = 0U;
                gHwaShutdownRequested = 0U;
                freeze = 1U;
            }
        } else {
#ifdef CONFIGURABLE_CAPTURE
#ifdef L3_RING_IQ8
        if (l3_captureUsesIq8()) {
            /* The completed IQ16 scratch frame must be packed before scratch
             * can be reused, including the final retained post frame. */
            if (gHwaFreezeRequested && !gActiveFrameIsPost) {
                gPostCaptureStarted = 1U;
            }
            gHwaRearmPending = 1U;
            queue = 1U;
        } else if (gHwaFreezeRequested && gActiveFrameIsPost &&
                   gActiveFrameShouldKeep &&
                   gPostFramesCaptured >= gCapturePlan.postFrames) {
            gCaptureActive = 0U;
            gHwaFreezeRequested = 0U;
            gHwaFreezeCompletions++;
            freeze = 1U;
        } else {
            if (gHwaFreezeRequested && !gActiveFrameIsPost) {
                gPostCaptureStarted = 1U;
            }
            gHwaRearmPending = 1U;
            queue = 1U;
        }
#else
        if (gHwaFreezeRequested && gActiveFrameIsPost &&
            gActiveFrameShouldKeep &&
            gPostFramesCaptured >= gCapturePlan.postFrames) {
            gCaptureActive = 0U;
            gHwaFreezeRequested = 0U;
            gHwaFreezeCompletions++;
            freeze = 1U;
        } else {
            if (gHwaFreezeRequested && !gActiveFrameIsPost) {
                gPostCaptureStarted = 1U;
            }
            gHwaRearmPending = 1U;
            queue = 1U;
        }
#endif
#else
        if (gHwaFreezeRequested && gRingFrame >= gHwaFreezeTargetFrame) {
            gCaptureActive = 0U;
            gHwaFreezeRequested = 0U;
            gHwaFreezeCompletions++;
            freeze = 1U;
        } else {
            gHwaRearmPending = 1U;
            queue = 1U;
        }
#endif
        }
    }
    Hwi_restore(key);
    if (freeze && gHwaFreezeSemaphore != NULL) {
        Semaphore_post(gHwaFreezeSemaphore);
    } else if (queue && gHwaRearmSemaphore != NULL) {
        Semaphore_post(gHwaRearmSemaphore);
    }
}

static void l3_hwaChainDoneCB(void *arg)
{
    (void)arg;
    gHwaFrameDone++;
    gHwaDoneSeen = 1U;
    l3_hwaMaybeQueueRearm();
}

static void l3_hwaOutputDoneCB(uintptr_t arg, uint8_t tcCode)
{
    (void)arg;
    (void)tcCode;
    gHwaOutputDone++;
    gRingFrame++;
#ifdef CONFIGURABLE_CAPTURE
#ifdef L3_RING_IQ8
    if (l3_captureUsesIq8() && gActiveFrameShouldKeep) {
        uint32_t completedSlot = gActiveFrameIsPost
                                     ? gCapturePlan.preFrames + gPostFramesCaptured
                                     : gPreFramesCaptured % gCapturePlan.preFrames;
        if (gIq8Pending) {
            gIq8PackOverruns++;
        }
        gIq8PendingSlot = completedSlot;
        gIq8PendingScratch = gIq8ActiveScratch;
        gIq8Pending = 1U;
    }
#endif
    if (gActiveFrameIsPost) {
        gPostFramesObserved++;
        if (gActiveFrameShouldKeep) {
            gPostFramesCaptured++;
        }
    } else {
        gPreFramesCaptured++;
        if ((gPreFramesCaptured % gCapturePlan.preFrames) == 0U) {
            gNumWrap++;
        }
    }
#else
    if ((gRingFrame % RING_FRAMES) == 0U) {
        gNumWrap++;
    }
#endif
    gHwaOutputSeen = 1U;
    l3_hwaMaybeQueueRearm();
}

static int32_t l3_hwaStartRing(void)
{
    int32_t errCode;

    errCode = HWA_enable(gHwaHandle, 1U);
    if (errCode != 0) {
        return errCode;
    }
    errCode = HWA_setDMA2ACCManualTrig(gHwaHandle, L3_HWA_PARAM_DUMMY_PING);
    if (errCode != 0) {
        (void)HWA_enable(gHwaHandle, 0U);
        return errCode;
    }
    errCode = HWA_setDMA2ACCManualTrig(gHwaHandle, L3_HWA_PARAM_DUMMY_PONG);
    if (errCode != 0) {
        (void)HWA_enable(gHwaHandle, 0U);
        return errCode;
    }
    gHwaArmedForFrame = 1U;
    return 0;
}

static int32_t l3_configHwaProcessParam(uint8_t paramIdx, uint8_t outChannel,
                                        uint16_t dstAddr)
{
    HWA_ParamConfig paramCfg;
    HWA_InterruptConfig interruptCfg;
    uint8_t dmaChannel;
    int32_t errCode;

    memset((void *)&paramCfg, 0, sizeof(paramCfg));
    paramCfg.triggerMode = HWA_TRIG_MODE_DFE;
    paramCfg.accelMode = HWA_ACCELMODE_FFT;
    paramCfg.source.srcAddr = 0U;
    paramCfg.source.srcAcnt = N_SAMPLES - 1U;
    paramCfg.source.srcAIdx = HWA_COMPLEX16_BYTES;
    paramCfg.source.srcBcnt = N_RX - 1U;
    paramCfg.source.srcBIdx = N_SAMPLES * HWA_COMPLEX16_BYTES;
    paramCfg.source.srcRealComplex = HWA_SAMPLES_FORMAT_COMPLEX;
    paramCfg.source.srcWidth = HWA_SAMPLES_WIDTH_16BIT;
    paramCfg.source.srcSign = HWA_SAMPLES_SIGNED;
    paramCfg.source.srcConjugate = HWA_FEATURE_BIT_DISABLE;
    paramCfg.source.srcScale = 0U;
    paramCfg.source.bpmEnable = HWA_FEATURE_BIT_DISABLE;

    /* RX-major output keeps each compact frame contiguous. EDMA crops the
     * configured window from each of the four 128-bin RX blocks. */
    paramCfg.dest.dstAddr = dstAddr;
    paramCfg.dest.dstAcnt = N_SAMPLES - 1U;
    paramCfg.dest.dstAIdx = HWA_COMPLEX16_BYTES;
    paramCfg.dest.dstBIdx = N_SAMPLES * HWA_COMPLEX16_BYTES;
    paramCfg.dest.dstRealComplex = HWA_SAMPLES_FORMAT_COMPLEX;
    paramCfg.dest.dstWidth = HWA_SAMPLES_WIDTH_16BIT;
    paramCfg.dest.dstSign = HWA_SAMPLES_SIGNED;
    paramCfg.dest.dstConjugate = HWA_FEATURE_BIT_DISABLE;
#ifdef L3_RING_IQ8
    /* EDMA compaction copies the low byte of each signed HWA result, so the
     * configurable HWA shift performs the quantization before that copy. */
#ifdef L3_IQ8_EDMA_PACK
    paramCfg.dest.dstScale =
        l3_captureUsesIq8() ? gIq8FixedShift : 0U;
#else
    paramCfg.dest.dstScale =
        l3_captureUsesIq8() ? L3_IQ8_HWA_SHIFT : 0U;
#endif
#else
    paramCfg.dest.dstScale = 0U;
#endif
    paramCfg.dest.dstSkipInit = 0U;
    paramCfg.accelModeArgs.fftMode.fftEn = HWA_FEATURE_BIT_ENABLE;
    paramCfg.accelModeArgs.fftMode.fftSize = (uint8_t)l3_log2_u32(N_SAMPLES);
    paramCfg.accelModeArgs.fftMode.butterflyScaling = 0x7FU;
    paramCfg.accelModeArgs.fftMode.interfZeroOutEn = HWA_FEATURE_BIT_DISABLE;
    paramCfg.accelModeArgs.fftMode.windowEn = HWA_FEATURE_BIT_DISABLE;
    paramCfg.accelModeArgs.fftMode.windowStart = 0U;
    paramCfg.accelModeArgs.fftMode.winSymm = HWA_FFT_WINDOW_NONSYMMETRIC;
    paramCfg.accelModeArgs.fftMode.winInterpolateMode = HWA_FFT_WINDOW_INTERPOLATE_MODE_NONE;
    paramCfg.accelModeArgs.fftMode.magLogEn = HWA_FFT_MODE_MAGNITUDE_LOG2_DISABLED;
    paramCfg.accelModeArgs.fftMode.fftOutMode = HWA_FFT_MODE_OUTPUT_DEFAULT;
    paramCfg.complexMultiply.mode = HWA_COMPLEX_MULTIPLY_MODE_DISABLE;

    errCode = HWA_configParamSet(gHwaHandle, paramIdx, &paramCfg, NULL);
    if (errCode != 0) {
        return errCode;
    }
    errCode = HWA_getDMAChanIndex(gHwaHandle, outChannel, &dmaChannel);
    if (errCode != 0) {
        return errCode;
    }
    memset((void *)&interruptCfg, 0, sizeof(interruptCfg));
    interruptCfg.interruptTypeFlag = HWA_PARAMDONE_INTERRUPT_TYPE_DMA;
    interruptCfg.dma.dstChannel = dmaChannel;
    return HWA_enableParamSetInterrupt(gHwaHandle, paramIdx, &interruptCfg);
}

static int32_t l3_configHwaOutputEdma(uint8_t channel, uint16_t shadow,
                                      uint32_t source, uint32_t destination,
                                      uintptr_t callbackArg, uint16_t binCount)
{
    EDMA_channelConfig_t channelCfg;
    EDMA_paramConfig_t shadowCfg;
    EDMA_paramSetConfig_t *param;

    memset((void *)&channelCfg, 0, sizeof(channelCfg));
    channelCfg.channelId = channel;
    channelCfg.channelType = (uint8_t)EDMA3_CHANNEL_TYPE_DMA;
    channelCfg.paramId = channel;
    channelCfg.eventQueueId = 0U;
    channelCfg.transferCompletionCallbackFxn =
        (callbackArg != 0U) ? l3_hwaOutputDoneCB : NULL;
    channelCfg.transferCompletionCallbackFxnArg = callbackArg;

    param = &channelCfg.paramSetConfig;
    param->sourceAddress = SOC_translateAddress(source, SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    param->destinationAddress = SOC_translateAddress(destination, SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    param->aCount = (uint16_t)(binCount * HWA_COMPLEX16_BYTES);
    param->bCount = (uint16_t)N_RX;
    param->cCount =
#ifdef CONFIGURABLE_CAPTURE
        (uint16_t)(gCapturePlan.chirpsPerFrame / 2U);
#else
        (uint16_t)(CHIRPS_PER_FRAME / 2U);
#endif
    param->bCountReload = (uint16_t)N_RX;
    param->sourceBindex = (int16_t)(N_SAMPLES * HWA_COMPLEX16_BYTES);
    param->destinationBindex = (int16_t)(binCount * HWA_COMPLEX16_BYTES);
    param->sourceCindex = 0;
    param->destinationCindex =
        (int16_t)(2U * N_RX * binCount * HWA_COMPLEX16_BYTES);
    param->linkAddress = EDMA_NULL_LINK_ADDRESS;
    param->transferCompletionCode = L3_HWA_SIGNATURE_CHANNEL;
    param->transferType = (uint8_t)EDMA3_SYNC_AB;
    param->sourceAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    param->destinationAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    param->fifoWidth = (uint8_t)EDMA3_FIFO_WIDTH_8BIT;
    param->isStaticSet = false;
    param->isEarlyCompletion = false;
    param->isFinalTransferInterruptEnabled = (callbackArg != 0U);
    param->isIntermediateTransferInterruptEnabled = false;
    param->isFinalChainingEnabled = true;
    param->isIntermediateChainingEnabled = true;

    if (EDMA_configChannel(gEdmaHandle, &channelCfg, true) != EDMA_NO_ERROR) {
        return -1;
    }
    memset((void *)&shadowCfg, 0, sizeof(shadowCfg));
    memcpy((void *)&shadowCfg.paramSetConfig, (void *)param, sizeof(*param));
    shadowCfg.transferCompletionCallbackFxn =
        (callbackArg != 0U) ? l3_hwaOutputDoneCB : NULL;
    shadowCfg.transferCompletionCallbackFxnArg = callbackArg;
    if (EDMA_configParamSet(gEdmaHandle, shadow, &shadowCfg) != EDMA_NO_ERROR ||
        EDMA_linkParamSets(gEdmaHandle, channel, shadow) != EDMA_NO_ERROR ||
        EDMA_linkParamSets(gEdmaHandle, shadow, shadow) != EDMA_NO_ERROR) {
        return -1;
    }
    return 0;
}

static int32_t l3_configHwaSignatureEdma(void)
{
    HWA_SrcDMAConfig trigger[2];
    EDMA_channelConfig_t channelCfg;
    EDMA_paramConfig_t shadowCfg;
    EDMA_paramSetConfig_t *param;

    if (HWA_getDMAconfig(gHwaHandle, L3_HWA_PARAM_DUMMY_PING, &trigger[0]) != 0 ||
        HWA_getDMAconfig(gHwaHandle, L3_HWA_PARAM_DUMMY_PONG, &trigger[1]) != 0) {
        return -1;
    }
    memset((void *)&channelCfg, 0, sizeof(channelCfg));
    channelCfg.channelId = L3_HWA_SIGNATURE_CHANNEL;
    channelCfg.channelType = (uint8_t)EDMA3_CHANNEL_TYPE_DMA;
    channelCfg.paramId = L3_HWA_SIGNATURE_CHANNEL;
    channelCfg.eventQueueId = 0U;
    param = &channelCfg.paramSetConfig;
    param->sourceAddress = SOC_translateAddress(trigger[0].srcAddr,
                                                SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    param->destinationAddress = SOC_translateAddress(trigger[0].destAddr,
                                                     SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    param->aCount = trigger[0].aCnt;
    param->bCount = (uint16_t)(trigger[0].bCnt * 2U);
    param->cCount = trigger[0].cCnt;
    param->bCountReload = param->bCount;
    param->sourceBindex = (int16_t)(trigger[1].srcAddr - trigger[0].srcAddr);
    param->destinationBindex = 0;
    param->sourceCindex = 0;
    param->destinationCindex = 0;
    param->linkAddress = EDMA_NULL_LINK_ADDRESS;
    param->transferCompletionCode = 0U;
    param->transferType = (uint8_t)EDMA3_SYNC_A;
    param->sourceAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    param->destinationAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    param->fifoWidth = (uint8_t)EDMA3_FIFO_WIDTH_8BIT;
    param->isStaticSet = false;
    param->isEarlyCompletion = false;
    if (EDMA_configChannel(gEdmaHandle, &channelCfg, false) != EDMA_NO_ERROR) {
        return -1;
    }
    memset((void *)&shadowCfg, 0, sizeof(shadowCfg));
    memcpy((void *)&shadowCfg.paramSetConfig, (void *)param, sizeof(*param));
    if (EDMA_configParamSet(gEdmaHandle, L3_HWA_SIGNATURE_SHADOW, &shadowCfg) != EDMA_NO_ERROR ||
        EDMA_linkParamSets(gEdmaHandle, L3_HWA_SIGNATURE_CHANNEL,
                          L3_HWA_SIGNATURE_SHADOW) != EDMA_NO_ERROR ||
        EDMA_linkParamSets(gEdmaHandle, L3_HWA_SIGNATURE_SHADOW,
                          L3_HWA_SIGNATURE_SHADOW) != EDMA_NO_ERROR) {
        return -1;
    }
    return 0;
}

static int32_t l3_configHwaCommon(void)
{
    HWA_CommonConfig commonCfg;

    memset((void *)&commonCfg, 0, sizeof(commonCfg));
    commonCfg.configMask = HWA_COMMONCONFIG_MASK_NUMLOOPS |
                           HWA_COMMONCONFIG_MASK_PARAMSTARTIDX |
                           HWA_COMMONCONFIG_MASK_PARAMSTOPIDX |
                           HWA_COMMONCONFIG_MASK_FFT1DENABLE |
                           HWA_COMMONCONFIG_MASK_INTERFERENCETHRESHOLD;
    commonCfg.numLoops =
#ifdef CONFIGURABLE_CAPTURE
        gCapturePlan.chirpsPerFrame / 2U;
#else
        CHIRPS_PER_FRAME / 2U;
#endif
    commonCfg.paramStartIdx = L3_HWA_PARAM_DUMMY_PING;
    commonCfg.paramStopIdx = L3_HWA_PARAM_FFT_PONG;
    commonCfg.fftConfig.fft1DEnable = HWA_FEATURE_BIT_ENABLE;
    commonCfg.fftConfig.interferenceThreshold = 0xFFFFFFU;
    return HWA_configCommon(gHwaHandle, &commonCfg);
}

#ifdef L3_DUMP_IQ8
static int8_t l3_quantizeIq8(int16_t sample, uint16_t scale)
{
    int32_t value = (int32_t)sample;
    int32_t half = (int32_t)scale / 2;
    int32_t quantized;

    if (value >= 0) {
        quantized = (value + half) / (int32_t)scale;
    } else {
        quantized = -((-value + half) / (int32_t)scale);
    }
    if (quantized > 127) {
        quantized = 127;
    } else if (quantized < -128) {
        quantized = -128;
    }
    return (int8_t)quantized;
}
#endif

#ifdef L3_RING_IQ8
#ifndef L3_IQ8_EDMA_PACK
#ifdef L3_IQ8_SPARSE_SCALE
static uint8_t l3_iq8SampledPackShift(const int16_t *source,
                                      uint32_t components)
{
    uint32_t maxAbs = 1U;
    uint32_t component;
    uint32_t step = 2U * L3_IQ8_SCALE_COMPLEX_STRIDE;
    uint8_t shift = 0U;

    for (component = 0U; component + 1U < components; component += step) {
        int32_t iValue = (int32_t)source[component];
        int32_t qValue = (int32_t)source[component + 1U];
        uint32_t iMagnitude =
            (iValue < 0) ? (uint32_t)(-iValue) : (uint32_t)iValue;
        uint32_t qMagnitude =
            (qValue < 0) ? (uint32_t)(-qValue) : (uint32_t)qValue;

        if (iMagnitude > maxAbs) {
            maxAbs = iMagnitude;
        }
        if (qMagnitude > maxAbs) {
            maxAbs = qMagnitude;
        }
    }
    while (maxAbs > 127U) {
        maxAbs = (maxAbs + 1U) >> 1U;
        shift++;
    }
    return shift;
}
#endif

#ifndef L3_IQ8_SPARSE_SCALE
static uint8_t l3_iq8PackShift(const int16_t *source, uint32_t components)
{
    uint32_t maxAbs = 1U;
    uint32_t component;
    uint8_t shift = 0U;

    for (component = 0U; component < components; component++) {
        int32_t value = (int32_t)source[component];
        uint32_t magnitude =
            (value < 0) ? (uint32_t)(-value) : (uint32_t)value;
        if (magnitude > maxAbs) {
            maxAbs = magnitude;
        }
    }
    while (maxAbs > 127U) {
        maxAbs = (maxAbs + 1U) >> 1U;
        shift++;
    }
    return shift;
}
#endif

static int8_t l3_quantizeIq8Shift(int16_t sample, uint8_t shift,
                                  uint32_t *clippedComponents)
{
    int32_t value = (int32_t)sample;
    int32_t quantized;

    if (shift == 0U) {
        quantized = value;
    } else {
        int32_t half = (int32_t)(1U << (shift - 1U));
        if (value >= 0) {
            quantized = (value + half) >> shift;
        } else {
            quantized = -((-value + half) >> shift);
        }
    }
    if (quantized > 127) {
        quantized = 127;
        if (clippedComponents != NULL) {
            (*clippedComponents)++;
        }
    } else if (quantized < -128) {
        quantized = -128;
        if (clippedComponents != NULL) {
            (*clippedComponents)++;
        }
    }
    return (int8_t)quantized;
}

static void l3_packIq8CompletedFrame(uint32_t slot, uint8_t scratch)
{
    const int16_t *source = &g_iq16FrameScratch[scratch][0];
    int8_t *destination = (int8_t *)&g_ring[gFrameOffset[slot]];
    uint32_t components = gFrameBytes[slot];
    uint32_t component;
#ifdef L3_IQ8_SPARSE_SCALE
    uint32_t clippedComponents = 0U;
    uint8_t packShift = l3_iq8SampledPackShift(source, components);
#else
    uint8_t packShift = l3_iq8PackShift(source, components);
#endif

    for (component = 0U; component < components; component++) {
#ifdef L3_IQ8_SPARSE_SCALE
        destination[component] =
            l3_quantizeIq8Shift(source[component], packShift,
                                &clippedComponents);
#else
        destination[component] =
            l3_quantizeIq8Shift(source[component], packShift, NULL);
#endif
    }
#ifdef L3_IQ8_SPARSE_SCALE
    gIq8ClippedComponents += clippedComponents;
#endif
    gFrameIq8Scale[slot] =
        (uint16_t)(L3_IQ8_HWA_SCALE << packShift);
    gIq8PackFrames++;
}
#else
static void l3_iq8EdmaDoneCB(uintptr_t arg, uint8_t tcCode)
{
    uint8_t scratch = (uint8_t)(arg - 1U);

    (void)tcCode;
    if (scratch < 2U) {
        gIq8EdmaBusy[scratch] = 0U;
        gIq8EdmaDone++;
        gIq8PackFrames++;
    } else {
        gIq8EdmaErrors++;
    }
    if (gIq8EdmaDoneSemaphore != NULL) {
        Semaphore_post(gIq8EdmaDoneSemaphore);
    }
}

static int32_t l3_startIq8EdmaPack(uint32_t slot, uint8_t scratch)
{
    EDMA_channelConfig_t channelCfg;
    EDMA_paramSetConfig_t *param;
    uint8_t channel;
    uint32_t components;
    int32_t errCode;

    if (slot >= gCapturePlan.totalFrames || scratch >= 2U) {
        return -1;
    }
    components = gFrameBytes[slot];
    if (components == 0U || components > 0xFFFFU) {
        return -1;
    }
    channel = scratch == 0U ? L3_IQ8_PACK_PING_CHANNEL
                            : L3_IQ8_PACK_PONG_CHANNEL;

    memset((void *)&channelCfg, 0, sizeof(channelCfg));
    channelCfg.channelId = channel;
    channelCfg.channelType = (uint8_t)EDMA3_CHANNEL_TYPE_DMA;
    channelCfg.paramId = channel;
    channelCfg.eventQueueId = 1U;
    channelCfg.transferCompletionCallbackFxn = l3_iq8EdmaDoneCB;
    channelCfg.transferCompletionCallbackFxnArg = (uintptr_t)(scratch + 1U);

    param = &channelCfg.paramSetConfig;
    param->sourceAddress = SOC_translateAddress(
        (uint32_t)&g_iq16FrameScratch[scratch][0],
        SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    param->destinationAddress = SOC_translateAddress(
        (uint32_t)&g_ring[gFrameOffset[slot]],
        SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    param->aCount = 1U;
    param->bCount = (uint16_t)components;
    param->cCount = 1U;
    param->bCountReload = (uint16_t)components;
    param->sourceBindex = (int16_t)sizeof(int16_t);
    param->destinationBindex = 1;
    param->sourceCindex = 0;
    param->destinationCindex = 0;
    param->linkAddress = EDMA_NULL_LINK_ADDRESS;
    param->transferCompletionCode = channel;
    param->transferType = (uint8_t)EDMA3_SYNC_AB;
    param->sourceAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    param->destinationAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    param->fifoWidth = (uint8_t)EDMA3_FIFO_WIDTH_8BIT;
    param->isStaticSet = true;
    param->isEarlyCompletion = false;
    param->isFinalTransferInterruptEnabled = true;
    param->isIntermediateTransferInterruptEnabled = false;
    param->isFinalChainingEnabled = false;
    param->isIntermediateChainingEnabled = false;

    (void)EDMA_disableChannel(gEdmaHandle, channel, EDMA3_CHANNEL_TYPE_DMA);
    gFrameIq8Scale[slot] = gIq8FixedScale;
    gIq8EdmaBusy[scratch] = 1U;
    errCode = EDMA_configChannel(gEdmaHandle, &channelCfg, false);
    if (errCode == EDMA_NO_ERROR) {
        errCode = EDMA_startDmaTransfer(gEdmaHandle, channel);
    }
    if (errCode != EDMA_NO_ERROR) {
        gIq8EdmaBusy[scratch] = 0U;
        gIq8EdmaErrors++;
        return -1;
    }
    return 0;
}

static void l3_waitForIq8EdmaScratch(uint8_t scratch)
{
    uint8_t waited = 0U;

    while (scratch < 2U && gIq8EdmaBusy[scratch]) {
        if (!waited) {
            gIq8EdmaWaits++;
            waited = 1U;
        }
        Semaphore_pend(gIq8EdmaDoneSemaphore, BIOS_WAIT_FOREVER);
    }
}

static void l3_waitForAllIq8Edma(void)
{
    l3_waitForIq8EdmaScratch(0U);
    l3_waitForIq8EdmaScratch(1U);
}
#endif
#endif

static uint32_t l3_snapshotBinStartForNextFrame(void)
{
#ifdef CONFIGURABLE_CAPTURE
    if (!gPostCaptureStarted) {
        return gCapturePlan.preStart;
    }
    if (gCapturePlan.phased &&
        gPostFramesCaptured < gCapturePlan.impactFrames) {
        return gCapturePlan.impactStart;
    }
    if ((!gCapturePlan.phased &&
         gPostFramesCaptured < (gCapturePlan.postFrames / 2U)) ||
        (gCapturePlan.phased &&
         (gPostFramesCaptured - gCapturePlan.impactFrames) <
             (gCapturePlan.ballFrames / 2U))) {
        return gCapturePlan.postStart;
    }
    return gCapturePlan.lateStart;
#else
#ifdef SNAPSHOT_DYNAMIC_WINDOWS
    uint32_t completedAfterRequest;

    if (!gHwaFreezeRequested) {
        return SNAPSHOT_BIN_START;
    }
    completedAfterRequest = gRingFrame - gHwaFreezeRequestFrame;
    if (completedAfterRequest < (HWA_POST_TRIGGER_FRAMES / 2U)) {
        return SNAPSHOT_MIDDLE_BIN_START;
    }
    return SNAPSHOT_LATE_BIN_START;
#else
    return SNAPSHOT_BIN_START;
#endif
#endif
}

static int32_t l3_configHwaFrameOutput(uint32_t ringSlot)
{
    uint32_t binStart = l3_snapshotBinStartForNextFrame();
    uint16_t binCount;
    uint32_t destination;
    uint32_t pingSource = SOC_XWR68XX_MSS_HWA_MEM2_BASE_ADDRESS +
                          binStart * HWA_COMPLEX16_BYTES;
    uint32_t pongSource = SOC_XWR68XX_MSS_HWA_MEM2_BASE_ADDRESS + HWA_MEM_STRIDE +
                          binStart * HWA_COMPLEX16_BYTES;

#ifdef CONFIGURABLE_CAPTURE
    if (gPostCaptureStarted) {
        ringSlot = gCapturePlan.preFrames + gPostFramesCaptured;
        gActiveFrameIsPost = 1U;
        if (gCapturePlan.phased &&
            gPostFramesCaptured < gCapturePlan.impactFrames) {
            binCount = gCapturePlan.impactBins;
            gActiveFrameShouldKeep = 1U;
        } else {
            uint32_t ballObserved = gCapturePlan.phased
                                        ? gPostFramesObserved -
                                              gCapturePlan.impactFrames
                                        : gPostFramesObserved;
            binCount = gCapturePlan.postBins;
            gActiveFrameShouldKeep =
                ((ballObserved % gCapturePlan.postStride) == 0U);
        }
    } else {
        ringSlot = gPreFramesCaptured % gCapturePlan.preFrames;
        binCount = gCapturePlan.preBins;
        gActiveFrameIsPost = 0U;
        gActiveFrameShouldKeep = 1U;
    }
    if (ringSlot >= gCapturePlan.totalFrames) {
        return -1;
    }
#ifdef L3_RING_IQ8
    destination = l3_captureUsesIq8()
                      ? (uint32_t)&g_iq16FrameScratch[gIq8ActiveScratch][0]
                      : (uint32_t)&g_ring[gFrameOffset[ringSlot]];
#else
    destination = (uint32_t)&g_ring[gFrameOffset[ringSlot]];
#endif
#else
    binCount = SNAPSHOT_BINS;
    destination = (uint32_t)&g_ring[ringSlot % RING_FRAMES][0];
#ifdef SNAPSHOT_DYNAMIC_WINDOWS
    gFrameBinStart[ringSlot % RING_FRAMES] = (uint8_t)binStart;
#endif
#endif

    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_OUT_PING_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_OUT_PONG_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    if (l3_configHwaOutputEdma(L3_HWA_OUT_PING_CHANNEL, L3_HWA_OUT_PING_SHADOW,
                              pingSource, destination, 0U, binCount) != 0 ||
        l3_configHwaOutputEdma(L3_HWA_OUT_PONG_CHANNEL, L3_HWA_OUT_PONG_SHADOW,
                              pongSource,
                              destination + N_RX * binCount * HWA_COMPLEX16_BYTES,
                              1U, binCount) != 0) {
        return -1;
    }
    return 0;
}

static void l3_drainHwaRearmSemaphore(void)
{
    if (gHwaRearmSemaphore != NULL) {
        while (Semaphore_pend(gHwaRearmSemaphore, BIOS_NO_WAIT)) {
            /* A completed frame may have posted immediately before capture was
             * frozen. It must not reconfigure the freshly armed chain. */
        }
    }
}

static int32_t l3_restartCompletedHwaFrame(void)
{
    uintptr_t key;
    int32_t errCode;

    (void)HWA_disableDoneInterrupt(gHwaHandle);
    (void)HWA_enable(gHwaHandle, 0U);
    key = Hwi_disable();
    gHwaDoneSeen = 0U;
    gHwaOutputSeen = 0U;
    gHwaRearmPending = 0U;
#ifdef L3_RING_IQ8
    gIq8Pending = 0U;
#endif
    Hwi_restore(key);
    errCode = l3_configHwaCommon();
    if (errCode == 0) {
        errCode =
#ifdef CONFIGURABLE_CAPTURE
            l3_configHwaFrameOutput(0U);
#else
            l3_configHwaFrameOutput(gRingFrame % RING_FRAMES);
#endif
    }
    if (errCode == 0) {
        errCode = HWA_enableDoneInterrupt(gHwaHandle, l3_hwaChainDoneCB, NULL);
    }
    if (errCode == 0) {
        errCode = l3_hwaStartRing();
    }
    return errCode;
}

static int32_t l3_freezeHwaAfterPostFrames(void)
{
    uintptr_t key;

    if (gHwaFreezeSemaphore == NULL) {
        return -1;
    }
    while (Semaphore_pend(gHwaFreezeSemaphore, BIOS_NO_WAIT)) {
        /* Discard a stale completion before issuing a new request. */
    }
    key = Hwi_disable();
    gHwaFreezeRequested = 1U;
    gHwaFreezeRequestFrame = gRingFrame;
#ifdef CONFIGURABLE_CAPTURE
    gPostCaptureStarted = 0U;
    gPostFramesCaptured = 0U;
    gPostFramesObserved = 0U;
    gActiveFrameShouldKeep = 1U;
    gHwaFreezeTargetFrame = 0U;
#else
    gHwaFreezeTargetFrame = gRingFrame + HWA_POST_TRIGGER_FRAMES;
#endif
    gHwaFreezeRequests++;
    Hwi_restore(key);
    /* Allow the configured post-trigger frames plus scheduling/stop margin. */
    if (!Semaphore_pend(gHwaFreezeSemaphore, 250U)) {
        key = Hwi_disable();
        gHwaFreezeRequested = 0U;
        gHwaFreezeTimeouts++;
        Hwi_restore(key);
        return -1;
    }
    return 0;
}

/* Application shutdown is not a shot trigger. Cancel any pending post-frame
 * plan and stop at the next completed HWA output instead of waiting for a
 * full post-impact movie that may never arrive while the RF chain is idle. */
static int32_t l3_freezeHwaForShutdown(void)
{
    uintptr_t key;

    if (gHwaFreezeSemaphore == NULL) {
        return -1;
    }
    while (Semaphore_pend(gHwaFreezeSemaphore, BIOS_NO_WAIT)) {
        /* Discard a stale completion before issuing a new request. */
    }
    key = Hwi_disable();
    gHwaFreezeRequested = 0U;
    gHwaShutdownRequested = 1U;
    Hwi_restore(key);

    /* Handle a frame that completed just before the shutdown request. */
    l3_hwaMaybeQueueRearm();
    if (!Semaphore_pend(gHwaFreezeSemaphore, 250U)) {
        key = Hwi_disable();
        gHwaShutdownRequested = 0U;
        gHwaFreezeTimeouts++;
        Hwi_restore(key);
        return -1;
    }
    return 0;
}

static int32_t l3_finishCaptureStop(void)
{
    int32_t errCode;

    if (MMWave_stop(gMMWaveHandle, &errCode) < 0) {
        CLI_write("Error: MMWave_stop failed (%d)\n", errCode);
        return -1;
    }
    Task_sleep(10);
#ifdef HWA_CHAINED_SNAPSHOT_RING
    (void)HWA_enable(gHwaHandle, 0U);
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_OUT_PING_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_OUT_PONG_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_SIGNATURE_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
#else
    (void)EDMA_disableChannel(gEdmaHandle, L3_EDMA_CHANNEL, EDMA3_CHANNEL_TYPE_DMA);
#endif
    gCaptureActive = 0U;
    return 0;
}

/* Shot dumps preserve the configured post-impact movie before stopping. */
static int32_t l3_stopCaptureAtBoundary(void)
{
    if (!gCaptureActive) {
        return 0;
    }
#ifdef HWA_CHAINED_SNAPSHOT_RING
    if (l3_freezeHwaAfterPostFrames() != 0) {
        CLI_write("Error: HWA post-trigger frame freeze timed out\n");
        return -1;
    }
#endif
    return l3_finishCaptureStop();
}

/* sensorStop only needs a clean hardware boundary, not post-impact frames. */
static int32_t l3_stopCaptureForShutdown(void)
{
    if (!gCaptureActive) {
        return 0;
    }
#ifdef HWA_CHAINED_SNAPSHOT_RING
    if (l3_freezeHwaForShutdown() != 0) {
        CLI_write("Error: HWA shutdown boundary timed out\n");
        return -1;
    }
#endif
    return l3_finishCaptureStop();
}

static int32_t l3_armHwaChain(void)
{
    HWA_ParamConfig dummyCfg;
    uint16_t mem2Offset = (uint16_t)(SOC_XWR68XX_MSS_HWA_MEM2_BASE_ADDRESS -
                                     SOC_XWR68XX_MSS_HWA_MEM0_BASE_ADDRESS);
    uint16_t mem3Offset = (uint16_t)(mem2Offset + HWA_MEM_STRIDE);
    int32_t errCode;

    if (!gHwaOpened || gHwaHandle == NULL) {
        return -1;
    }
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_OUT_PING_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_OUT_PONG_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    (void)EDMA_disableChannel(gEdmaHandle, L3_HWA_SIGNATURE_CHANNEL,
                              EDMA3_CHANNEL_TYPE_DMA);
    (void)HWA_enable(gHwaHandle, 0U);
    gHwaDoneSeen = 0U;
    gHwaOutputSeen = 0U;
    gHwaRearmPending = 0U;
#ifdef SNAPSHOT_DYNAMIC_WINDOWS
    memset((void *)gFrameBinStart, SNAPSHOT_BIN_START, sizeof(gFrameBinStart));
#endif
    (void)HWA_disableDoneInterrupt(gHwaHandle);
    (void)HWA_disableParamSetInterrupt(gHwaHandle, L3_HWA_PARAM_FFT_PING,
                                      HWA_PARAMDONE_INTERRUPT_TYPE_CPU |
                                      HWA_PARAMDONE_INTERRUPT_TYPE_DMA);
    (void)HWA_disableParamSetInterrupt(gHwaHandle, L3_HWA_PARAM_FFT_PONG,
                                      HWA_PARAMDONE_INTERRUPT_TYPE_CPU |
                                      HWA_PARAMDONE_INTERRUPT_TYPE_DMA);
    l3_drainHwaRearmSemaphore();
    /* A dump can freeze the accelerator between ping and pong. Reset the HWA
     * state machine before replacing its paramsets; otherwise the second dump
     * can wedge in MMWave_stop while stale DFE/DMA triggers remain pending. */
    errCode = HWA_reset(gHwaHandle);
    if (errCode != 0) {
        return errCode;
    }

    memset((void *)&dummyCfg, 0, sizeof(dummyCfg));
    dummyCfg.triggerMode = HWA_TRIG_MODE_DMA;
    dummyCfg.dmaTriggerSrc = L3_HWA_PARAM_DUMMY_PING;
    dummyCfg.accelMode = HWA_ACCELMODE_NONE;
    errCode = HWA_configParamSet(gHwaHandle, L3_HWA_PARAM_DUMMY_PING, &dummyCfg, NULL);
    if (errCode != 0) {
        return errCode;
    }
    dummyCfg.dmaTriggerSrc = L3_HWA_PARAM_DUMMY_PONG;
    errCode = HWA_configParamSet(gHwaHandle, L3_HWA_PARAM_DUMMY_PONG, &dummyCfg, NULL);
    if (errCode != 0) {
        return errCode;
    }
    errCode = l3_configHwaProcessParam(L3_HWA_PARAM_FFT_PING,
                                      L3_HWA_OUT_PING_CHANNEL, mem2Offset);
    if (errCode != 0) {
        return errCode;
    }
    errCode = l3_configHwaProcessParam(L3_HWA_PARAM_FFT_PONG,
                                      L3_HWA_OUT_PONG_CHANNEL, mem3Offset);
    if (errCode != 0) {
        return errCode;
    }

    errCode = l3_configHwaCommon();
    if (errCode != 0) {
        return errCode;
    }

    if (l3_configHwaFrameOutput(0U) != 0 || l3_configHwaSignatureEdma() != 0) {
        return -1;
    }
    errCode = HWA_enableDoneInterrupt(gHwaHandle, l3_hwaChainDoneCB, NULL);
    if (errCode != 0) {
        return errCode;
    }
    return l3_hwaStartRing();
}

static void l3_hwaRearmTask(UArg arg0, UArg arg1)
{
    (void)arg0;
    (void)arg1;
    while (1) {
        Semaphore_pend(gHwaRearmSemaphore, BIOS_WAIT_FOREVER);
        {
            uintptr_t key;
            uint8_t shouldRearm = 0U;
            uint8_t freezeAfterPack = 0U;
#ifdef L3_RING_IQ8
            uint8_t hadPending = 0U;
            uint8_t pendingScratch = 0U;
            uint8_t nextScratch = 0U;
            uint32_t pendingSlot = 0U;
#endif
            int32_t errCode;

            key = Hwi_disable();
            if (gCaptureActive) {
                gHwaRearmBusy = 1U;
                shouldRearm = 1U;
            }
            Hwi_restore(key);
            if (!shouldRearm) {
                continue;
            }

#if !defined(L3_RING_IQ8)
            key = Hwi_disable();
            if (gHwaShutdownRequested) {
                gCaptureActive = 0U;
                gHwaShutdownRequested = 0U;
                gHwaRearmPending = 0U;
                freezeAfterPack = 1U;
            }
            Hwi_restore(key);
#else
            if (!l3_captureUsesIq8()) {
                key = Hwi_disable();
                if (gHwaShutdownRequested) {
                    gCaptureActive = 0U;
                    gHwaShutdownRequested = 0U;
                    gHwaRearmPending = 0U;
                    freezeAfterPack = 1U;
                }
                Hwi_restore(key);
            }
#endif
#ifdef L3_RING_IQ8
            if (l3_captureUsesIq8()) {
                key = Hwi_disable();
                if (gIq8Pending) {
                    hadPending = 1U;
                    pendingSlot = gIq8PendingSlot;
                    pendingScratch = gIq8PendingScratch;
                    gIq8Pending = 0U;
                }
                if (gHwaShutdownRequested) {
                    gCaptureActive = 0U;
                    gHwaShutdownRequested = 0U;
                    gHwaRearmPending = 0U;
                    freezeAfterPack = 1U;
                } else if (gHwaFreezeRequested && gActiveFrameIsPost &&
                    gActiveFrameShouldKeep &&
                    gPostFramesCaptured >= gCapturePlan.postFrames) {
                    gCaptureActive = 0U;
                    gHwaFreezeRequested = 0U;
                    gHwaFreezeCompletions++;
                    gHwaRearmPending = 0U;
                    freezeAfterPack = 1U;
                } else {
                    nextScratch = gIq8ActiveScratch ^ 1U;
#ifndef L3_IQ8_EDMA_PACK
                    gIq8ActiveScratch = nextScratch;
#endif
                }
                Hwi_restore(key);
            }
#endif
            if (freezeAfterPack) {
#ifdef L3_RING_IQ8
                if (hadPending) {
#ifdef L3_IQ8_EDMA_PACK
                    (void)l3_startIq8EdmaPack(pendingSlot, pendingScratch);
#else
                    l3_packIq8CompletedFrame(pendingSlot, pendingScratch);
#endif
                }
#ifdef L3_IQ8_EDMA_PACK
                if (l3_captureUsesIq8()) {
                    l3_waitForAllIq8Edma();
                }
#endif
#endif
                if (gHwaFreezeSemaphore != NULL) {
                    Semaphore_post(gHwaFreezeSemaphore);
                }
                key = Hwi_disable();
                gHwaRearmBusy = 0U;
                Hwi_restore(key);
                continue;
            }
#if defined(L3_RING_IQ8) && defined(L3_IQ8_EDMA_PACK)
            if (l3_captureUsesIq8()) {
                l3_waitForIq8EdmaScratch(nextScratch);
                gIq8ActiveScratch = nextScratch;
            }
#endif
            errCode = l3_restartCompletedHwaFrame();
            if (errCode == 0) {
                gHwaRearms++;
            } else {
                gHwaRearmErrors++;
            }
#ifdef L3_RING_IQ8
            if (l3_captureUsesIq8() && hadPending) {
#ifdef L3_IQ8_EDMA_PACK
                (void)l3_startIq8EdmaPack(pendingSlot, pendingScratch);
#else
                l3_packIq8CompletedFrame(pendingSlot, pendingScratch);
#endif
            }
#endif
            key = Hwi_disable();
            gHwaRearmBusy = 0U;
            Hwi_restore(key);
        }
    }
}
#endif

/* Fill the 20-byte fixed dump header (dump_format.h / iwr6843_l3dump.HEADER). */
static void l3_fill_header(l3_dump_header_t *h, uint16_t n_frames,
                           uint16_t trigger_frame)
{
    memcpy(h->magic, L3_DUMP_MAGIC, 4);
    h->version          = L3_DUMP_VERSION;
    h->n_frames         = n_frames;
    h->chirps_per_frame =
#ifdef CONFIGURABLE_CAPTURE
        gCapturePlan.chirpsPerFrame;
#else
        CHIRPS_PER_FRAME;
#endif
    h->n_tx             = N_TX;
    h->n_rx             = N_RX;
#ifdef SNAPSHOT_DUMP
#ifdef CONFIGURABLE_CAPTURE
#ifdef HYBRID_CADENCE_CAPTURE
    h->version          = L3_DUMP_VERSION_TIMED;
#ifdef L3_DUMP_IQ8
    h->sample_fmt       = L3_SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED;
#elif defined(L3_RING_IQ8)
    h->sample_fmt       = l3_captureUsesIq8()
                              ? L3_SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
                              : L3_SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED;
#else
    h->sample_fmt       = L3_SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED;
#endif
#else
    h->version          = L3_DUMP_VERSION_VARIABLE;
    h->sample_fmt       = L3_SAMPLE_RANGE_FFT_IQ16_VARIABLE;
#endif
    h->n_samples = gCapturePlan.preBins;
    if (gCapturePlan.impactBins > h->n_samples) {
        h->n_samples = gCapturePlan.impactBins;
    }
    if (gCapturePlan.postBins > h->n_samples) {
        h->n_samples = gCapturePlan.postBins;
    }
    h->_pad             = 0U;
#else
    h->n_samples        = SNAPSHOT_BINS;
#ifdef SNAPSHOT_DYNAMIC_WINDOWS
    h->version          = L3_DUMP_VERSION_WINDOWED;
    h->sample_fmt       = L3_SAMPLE_RANGE_FFT_IQ16_WINDOWED;
    h->_pad             = 0U;
#else
    h->sample_fmt       = L3_SAMPLE_RANGE_FFT_IQ16;
    h->_pad             = SNAPSHOT_BIN_START;
#endif
#endif
#else
    h->n_samples        = SAVE_SAMPLES;
    h->sample_fmt       = L3_SAMPLE_INT16_IQ;
    h->_pad             = 0;
#endif
    h->trigger_frame    = trigger_frame;
    h->frame_period_us  = gFramePeriodUs;
}

#ifdef CONFIGURABLE_CAPTURE
static void l3_writeFrameDescriptor(uint32_t slot, uint8_t firstFrame)
{
#ifdef HYBRID_CADENCE_CAPTURE
    uint8_t descriptor[4];
    uint16_t deltaUs = firstFrame ? 0U : gFrameDeltaUs[slot];

    descriptor[0] = gFrameBinStart[slot];
    descriptor[1] = gFrameBinCount[slot];
    descriptor[2] = (uint8_t)(deltaUs & 0xFFU);
    descriptor[3] = (uint8_t)(deltaUs >> 8U);
    UART_writePolling(gDataUart, descriptor, sizeof(descriptor));
#else
    uint8_t descriptor[2];
    (void)firstFrame;

    descriptor[0] = gFrameBinStart[slot];
    descriptor[1] = gFrameBinCount[slot];
    UART_writePolling(gDataUart, descriptor, sizeof(descriptor));
#endif
}
#endif

#if defined(CONFIGURABLE_CAPTURE) && defined(L3_DUMP_IQ8)
static uint16_t l3_iq8FrameScale(uint32_t slot)
{
    const int16_t *src = (const int16_t *)&g_ring[gFrameOffset[slot]];
    uint32_t words = gFrameBytes[slot] / (uint32_t)sizeof(int16_t);
    uint32_t maxAbs = 1U;
    uint32_t word;

    for (word = 0U; word < words; word++) {
        int32_t value = (int32_t)src[word];
        uint32_t magnitude =
            (value < 0) ? (uint32_t)(-value) : (uint32_t)value;
        if (magnitude > maxAbs) {
            maxAbs = magnitude;
        }
    }
    return (uint16_t)((maxAbs + 126U) / 127U);
}
#endif

#if defined(CONFIGURABLE_CAPTURE) && defined(L3_ANY_IQ8)
static void l3_writeU16Le(uint16_t value)
{
    uint8_t out[2];

    out[0] = (uint8_t)(value & 0xFFU);
    out[1] = (uint8_t)(value >> 8U);
    UART_writePolling(gDataUart, out, sizeof(out));
}
#endif

#if defined(CONFIGURABLE_CAPTURE) && defined(L3_DUMP_IQ8)
static void l3_writeCompressedIq8Frame(uint32_t slot, uint16_t scale)
{
    const int16_t *src = (const int16_t *)&g_ring[gFrameOffset[slot]];
    uint32_t words = gFrameBytes[slot] / (uint32_t)sizeof(int16_t);
    uint8_t out[256];
    uint32_t pending = 0U;
    uint32_t word;

    for (word = 0U; word < words; word++) {
        out[pending++] = (uint8_t)l3_quantizeIq8(src[word], scale);
        if (pending == sizeof(out)) {
            UART_writePolling(gDataUart, out, pending);
            pending = 0U;
        }
    }
    if (pending > 0U) {
        UART_writePolling(gDataUart, out, pending);
    }
}
#endif

static int32_t l3_readTemperatureReport(l3_temperature_report_t *report)
{
    rlRfTempData_t tempData;
    int32_t errCode;

    memset((void *)&tempData, 0, sizeof(tempData));
    errCode = rlRfGetTemperatureReport(RL_DEVICE_MAP_INTERNAL_BSS, &tempData);
    if (errCode != 0) {
        return errCode;
    }

    report->device_time_ms = tempData.time;
    report->tmpRx0Sens = tempData.tmpRx0Sens;
    report->tmpRx1Sens = tempData.tmpRx1Sens;
    report->tmpRx2Sens = tempData.tmpRx2Sens;
    report->tmpRx3Sens = tempData.tmpRx3Sens;
    report->tmpTx0Sens = tempData.tmpTx0Sens;
    report->tmpTx1Sens = tempData.tmpTx1Sens;
    report->tmpTx2Sens = tempData.tmpTx2Sens;
    report->tmpPmSens = tempData.tmpPmSens;
    report->tmpDig0Sens = tempData.tmpDig0Sens;
    report->tmpDig1Sens = tempData.tmpDig1Sens;
    return 0;
}

#ifndef HWA_CHAINED_SNAPSHOT_RING
/* EDMA ring-wrap completion (fires once per RING_CHIRPS; liveness only). */
static void l3_edmaCB(uintptr_t arg, uint8_t tcCode)
{
    (void)arg; (void)tcCode;
#ifdef LIVE_SNAPSHOT_RING
    {
        uintptr_t key;
        uint32_t bit = 1U << (uint32_t)arg;
        key = Hwi_disable();
        if ((gRawFrameReadyMask & bit) != 0U) {
            gRawFrameDrops++;
        }
        gRawFrameReadyMask |= bit;
        Hwi_restore(key);
    }
#else
    gNumWrap++;
#endif
}
#endif

/* Frame-start ISR: liveness + ring-alignment counters (the EDMA fills the
 * ring autonomously). */
static void l3_frameStartISR(uintptr_t arg)
{
    (void)arg;
    gNumFrame++;
#ifdef HWA_CHAINED_SNAPSHOT_RING
    if (gCaptureActive) {
        if (!gHwaArmedForFrame) {
            gHwaMissedFrameStarts++;
        }
        gHwaArmedForFrame = 0U;
    }
#endif
#if !defined(LIVE_SNAPSHOT_RING) && !defined(HWA_CHAINED_SNAPSHOT_RING)
    gRingFrame++;
#endif
}

/* Start the RF front-end (config must already be applied). Shared by
 * sensorStart and the post-dump restart. */
static int32_t l3_startFrontEnd(void)
{
    MMWave_CalibrationCfg calibrationCfg;
    int32_t               errCode;

    memset((void *)&calibrationCfg, 0, sizeof(calibrationCfg));
    calibrationCfg.dfeDataOutputMode                          = gCtrlCfg.dfeDataOutputMode;
    calibrationCfg.u.chirpCalibrationCfg.enableCalibration    = true;
    calibrationCfg.u.chirpCalibrationCfg.enablePeriodicity    = true;
    calibrationCfg.u.chirpCalibrationCfg.periodicTimeInFrames = 10U;
    return MMWave_start(gMMWaveHandle, &calibrationCfg, &errCode);
}

static uint8_t l3_dumpCancelRequested(void)
{
    UART_Config *uartConfig = (UART_Config *)gCliUart;
    UartSci_HwCfg *hwCfg;
    uint8_t value;

    if (uartConfig == NULL || uartConfig->hwAttrs == NULL) {
        return 0U;
    }
    hwCfg = (UartSci_HwCfg *)uartConfig->hwAttrs;
    if (CSL_FEXTR(hwCfg->ptrSCIRegs->SCIFLR, 9U, 9U) == 0U) {
        return 0U;
    }
    value = (uint8_t)CSL_FEXTR(hwCfg->ptrSCIRegs->SCIRD, 7U, 0U);
    return (value == L3_DUMP_CANCEL_BYTE) ? 1U : 0U;
}

/* CLI "l3dump": record the current circular position, retain the configured
 * post-trigger frames, stop at that completed frame boundary, stream the ring,
 * then restart from slot zero. */
int32_t l3_cli_dump(int32_t argc, char *argv[])
{
    l3_dump_header_t h;
    uint32_t         i;
    uint8_t          dumpCancelled = 0U;
#ifdef CONFIGURABLE_CAPTURE
    uint32_t actualPre;
    uint32_t actualPost;
    uint32_t oldestPre;
#endif
    (void)argc; (void)argv;

    if (!gCaptureActive) {
        return -1;
    }

    /* Halt chirping only after HWA and both output EDMAs completed naturally. */
    if (l3_stopCaptureAtBoundary() != 0) {
        return -1;
    }

    /* Oldest slot = time-order start (best-effort: a frame-start ISR racing
     * the stop can skew this by one; the host cross-checks with its own
     * rotation solve). Before the first wrap the oldest data is slot 0. */
#ifdef CONFIGURABLE_CAPTURE
    actualPre = (gPreFramesCaptured < gCapturePlan.preFrames)
                    ? gPreFramesCaptured : gCapturePlan.preFrames;
    actualPost = (gPostFramesCaptured < gCapturePlan.postFrames)
                     ? gPostFramesCaptured : gCapturePlan.postFrames;
    oldestPre = (gPreFramesCaptured >= gCapturePlan.preFrames)
                    ? (gPreFramesCaptured % gCapturePlan.preFrames) : 0U;
    l3_fill_header(&h, (uint16_t)(actualPre + actualPost), 0U);
#else
    l3_fill_header(&h, RING_FRAMES,
                   (gRingFrame >= RING_FRAMES)
                       ? (uint16_t)(gRingFrame % RING_FRAMES) : 0U);
#endif
    {
        l3_temperature_report_t tempReport;
        int32_t tempStatus;

        memset((void *)&tempReport, 0, sizeof(tempReport));
        tempStatus = l3_readTemperatureReport(&tempReport);
        if (tempStatus == 0) {
#ifdef CONFIGURABLE_CAPTURE
            h.version = L3_DUMP_VERSION_CAPTURE_TEMPERATURE;
#else
            h.version = L3_DUMP_VERSION_TEMPERATURE;
#endif
        }
        UART_writePolling(gDataUart, (uint8_t *)&h, sizeof(h));
        if (tempStatus == 0) {
            UART_writePolling(gDataUart, (uint8_t *)&tempReport, sizeof(tempReport));
        }
    }
#ifdef CONFIGURABLE_CAPTURE
    for (i = 0U; i < actualPre; i++) {
        uint32_t slot = (oldestPre + i) % gCapturePlan.preFrames;
        l3_writeFrameDescriptor(slot, (i == 0U));
    }
    for (i = 0U; i < actualPost; i++) {
        uint32_t slot = gCapturePlan.preFrames + i;
        l3_writeFrameDescriptor(slot, (actualPre == 0U && i == 0U));
    }
#ifdef L3_ANY_IQ8
    if (
#ifdef L3_DUMP_IQ8
        1U
#else
        l3_captureUsesIq8()
#endif
    )
    {
        for (i = 0U; i < actualPre; i++) {
            uint32_t slot = (oldestPre + i) % gCapturePlan.preFrames;
#ifdef L3_RING_IQ8
            l3_writeU16Le(gFrameIq8Scale[slot]);
#else
            uint32_t outputIndex = i;
            gFrameIq8Scale[outputIndex] = l3_iq8FrameScale(slot);
            l3_writeU16Le(gFrameIq8Scale[outputIndex]);
#endif
        }
        for (i = 0U; i < actualPost; i++) {
            uint32_t slot = gCapturePlan.preFrames + i;
#ifdef L3_RING_IQ8
            l3_writeU16Le(gFrameIq8Scale[slot]);
#else
            uint32_t outputIndex = actualPre + i;
            gFrameIq8Scale[outputIndex] = l3_iq8FrameScale(slot);
            l3_writeU16Le(gFrameIq8Scale[outputIndex]);
#endif
        }
    }
#endif
#else
#ifdef SNAPSHOT_DYNAMIC_WINDOWS
    UART_writePolling(gDataUart, gFrameBinStart, sizeof(gFrameBinStart));
#endif
#endif
#ifdef SNAPSHOT_DUMP
#if defined(LIVE_SNAPSHOT_RING) || defined(HWA_CHAINED_SNAPSHOT_RING)
#ifdef CONFIGURABLE_CAPTURE
    for (i = 0U; i < actualPre; i++) {
        uint32_t slot = (oldestPre + i) % gCapturePlan.preFrames;
#ifdef L3_RING_IQ8
        UART_writePolling(gDataUart, &g_ring[gFrameOffset[slot]],
                          gFrameBytes[slot]);
#elif defined(L3_DUMP_IQ8)
        l3_writeCompressedIq8Frame(slot, gFrameIq8Scale[i]);
#else
        UART_writePolling(gDataUart, &g_ring[gFrameOffset[slot]],
                          gFrameBytes[slot]);
#endif
        if (l3_dumpCancelRequested()) {
            dumpCancelled = 1U;
            break;
        }
    }
    for (i = 0U; !dumpCancelled && i < actualPost; i++) {
        uint32_t slot = gCapturePlan.preFrames + i;
#ifdef L3_RING_IQ8
        UART_writePolling(gDataUart, &g_ring[gFrameOffset[slot]],
                          gFrameBytes[slot]);
#elif defined(L3_DUMP_IQ8)
        l3_writeCompressedIq8Frame(slot, gFrameIq8Scale[actualPre + i]);
#else
        UART_writePolling(gDataUart, &g_ring[gFrameOffset[slot]],
                          gFrameBytes[slot]);
#endif
        if (l3_dumpCancelRequested()) {
            dumpCancelled = 1U;
            break;
        }
    }
#else
    for (i = 0; i < RING_FRAMES; i++) {
        UART_writePolling(gDataUart, (uint8_t *)g_ring[i], sizeof(g_ring[i]));
    }
#endif
#else
    if (!gHwaOpened || gHwaHandle == NULL) {
        CLI_write("Error: HWA unavailable for snapshot dump\n");
        gCaptureActive = 0U;
        return -1;
    }
    for (i = 0; i < RING_FRAMES; i++) {
        uint32_t chirp;
        for (chirp = 0U; chirp < CHIRPS_PER_FRAME; chirp++) {
            uint32_t rawWord = chirp * N_RX * N_SAMPLES * 2U;
            if (l3_emitSnapshotChirp(&g_ring[i][rawWord]) != 0) {
                CLI_write("Error: HWA snapshot emit failed\n");
                gCaptureActive = 0U;
                return -1;
            }
        }
    }
#endif
#else
    for (i = 0; i < RING_FRAMES; i++) {
        UART_writePolling(gDataUart, (uint8_t *)g_ring[i], sizeof(g_ring[i]));
    }
#endif

#ifdef HWA_CHAINED_SNAPSHOT_RING
    gRingFrame = 0U;
    gHwaFreezeRequestFrame = 0U;
    gHwaFreezeTargetFrame = 0U;
#ifdef CONFIGURABLE_CAPTURE
    gPreFramesCaptured = 0U;
    gPostFramesCaptured = 0U;
    gPostFramesObserved = 0U;
    gPostCaptureStarted = 0U;
    gActiveFrameIsPost = 0U;
    gActiveFrameShouldKeep = 1U;
#endif
    if (l3_restartCompletedHwaFrame() < 0) {
        CLI_write("Error: completed HWA frame restart failed\n");
        gCaptureActive = 0U;
        return -1;
    }
    gHwaFreezeRestarts++;
#else
    if (l3_armCapture() < 0) {
        CLI_write("Error: EDMA re-arm failed\n");
        gCaptureActive = 0U;
        return -1;
    }
#endif
#ifndef HWA_CHAINED_SNAPSHOT_RING
    gRingFrame = 0U;
#endif
#ifdef LIVE_SNAPSHOT_RING
    gRawFrameReadyMask = 0U;
    gRawFrameDrops     = 0U;
    gSnapshotFrames    = 0U;
    gSnapshotErrors    = 0U;
    gSnapshotBusy      = 0U;
    gHwaFftConfigured  = 0U;
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
    gCaptureActive = 1U;
#endif
    if (l3_startFrontEnd() < 0) {
        CLI_write("Error: RF restart failed\n");
        gCaptureActive = 0U;
        return -1;
    }
    if (dumpCancelled) {
        UART_writePolling(gDataUart, L3_DUMP_CANCEL_ACK, L3_DUMP_CANCEL_ACK_BYTES);
    }
    return 0;
}

/* CLI "stats": report capture counters (diagnostic). */
static int32_t l3_cli_stats(int32_t argc, char *argv[])
{
    (void)argc; (void)argv;
#ifdef LIVE_SNAPSHOT_RING
    CLI_write("frames=%u wraps=%u active=%d calib=0x%x rf_faults=%u "
              "snap_frames=%u snap_ready=0x%x snap_drops=%u snap_err=%u snap_busy=%u\n",
              (unsigned)gNumFrame, (unsigned)gNumWrap, (int)gCaptureActive,
              (unsigned)gCalibStatus, (unsigned)gRfFaults,
              (unsigned)gSnapshotFrames, (unsigned)gRawFrameReadyMask,
              (unsigned)gRawFrameDrops, (unsigned)gSnapshotErrors,
              (unsigned)gSnapshotBusy);
#else
#ifdef HWA_CHAINED_SNAPSHOT_RING
#ifdef CONFIGURABLE_CAPTURE
#ifdef L3_RING_IQ8
    CLI_write("frames=%u wraps=%u active=%d calib=0x%x rf_faults=%u "
              "hwa_frames=%u hwa_out=%u hwa_rearms=%u hwa_rearm_err=%u "
              "hwa_missed=%u freeze_req=%u freeze_done=%u freeze_to=%u "
              "format=%s plan=%upre/%upost loops=%u used=%u/%u\n",
              (unsigned)gNumFrame, (unsigned)gNumWrap, (int)gCaptureActive,
              (unsigned)gCalibStatus, (unsigned)gRfFaults,
              (unsigned)gHwaFrameDone, (unsigned)gHwaOutputDone,
              (unsigned)gHwaRearms, (unsigned)gHwaRearmErrors,
              (unsigned)gHwaMissedFrameStarts,
              (unsigned)gHwaFreezeRequests, (unsigned)gHwaFreezeCompletions,
              (unsigned)gHwaFreezeTimeouts,
              l3_captureUsesIq8() ? "iq8" : "iq16",
              (unsigned)gCapturePlan.preFrames,
              (unsigned)gCapturePlan.postFrames,
              (unsigned)gCapturePlan.loops,
              (unsigned)gCapturePlan.usedBytes,
              (unsigned)l3_captureCapacityBytes());
    CLI_write("iq8_packed=%u iq8_overrun=%u iq8_clipped=%u pending=%u pre_seen=%u "
              "post_kept=%u post_seen=%u stride=%u"
#ifdef L3_IQ8_EDMA_PACK
              " iq8_edma_done=%u iq8_edma_err=%u iq8_edma_wait=%u "
              "iq8_busy=%u/%u iq8_scale=%u"
#endif
              "\n",
              (unsigned)gIq8PackFrames,
              (unsigned)gIq8PackOverruns,
              (unsigned)gIq8ClippedComponents,
              (unsigned)gIq8Pending,
              (unsigned)gPreFramesCaptured,
              (unsigned)gPostFramesCaptured,
              (unsigned)gPostFramesObserved,
              (unsigned)gCapturePlan.postStride
#ifdef L3_IQ8_EDMA_PACK
              , (unsigned)gIq8EdmaDone,
              (unsigned)gIq8EdmaErrors,
              (unsigned)gIq8EdmaWaits,
              (unsigned)gIq8EdmaBusy[0],
              (unsigned)gIq8EdmaBusy[1],
              (unsigned)gIq8FixedScale
#endif
              );
#else
    CLI_write("frames=%u wraps=%u active=%d calib=0x%x rf_faults=%u "
              "hwa_frames=%u hwa_out=%u hwa_rearms=%u hwa_rearm_err=%u "
              "hwa_missed=%u hwa_wait=0x%x freeze_req=%u freeze_done=%u freeze_to=%u "
              "freeze_restart=%u plan=%upre/%upost bins=%u/%u loops=%u "
              "used=%u pre_seen=%u post_kept=%u post_seen=%u stride=%u\n",
              (unsigned)gNumFrame, (unsigned)gNumWrap, (int)gCaptureActive,
              (unsigned)gCalibStatus, (unsigned)gRfFaults,
              (unsigned)gHwaFrameDone, (unsigned)gHwaOutputDone,
              (unsigned)gHwaRearms, (unsigned)gHwaRearmErrors,
              (unsigned)gHwaMissedFrameStarts,
              (unsigned)((gHwaDoneSeen ? 1U : 0U) |
                         (gHwaOutputSeen ? 2U : 0U)),
              (unsigned)gHwaFreezeRequests, (unsigned)gHwaFreezeCompletions,
              (unsigned)gHwaFreezeTimeouts, (unsigned)gHwaFreezeRestarts,
              (unsigned)gCapturePlan.preFrames,
              (unsigned)gCapturePlan.postFrames,
              (unsigned)gCapturePlan.preBins,
              (unsigned)gCapturePlan.postBins,
              (unsigned)gCapturePlan.loops,
              (unsigned)gCapturePlan.usedBytes,
              (unsigned)gPreFramesCaptured,
              (unsigned)gPostFramesCaptured,
              (unsigned)gPostFramesObserved,
              (unsigned)gCapturePlan.postStride);
#endif
#else
    CLI_write("frames=%u wraps=%u active=%d calib=0x%x rf_faults=%u "
              "hwa_frames=%u hwa_out=%u hwa_rearms=%u hwa_rearm_err=%u "
              "hwa_missed=%u hwa_wait=0x%x freeze_req=%u freeze_done=%u freeze_to=%u "
              "freeze_restart=%u\n",
              (unsigned)gNumFrame, (unsigned)gNumWrap, (int)gCaptureActive,
              (unsigned)gCalibStatus, (unsigned)gRfFaults,
              (unsigned)gHwaFrameDone, (unsigned)gHwaOutputDone,
              (unsigned)gHwaRearms, (unsigned)gHwaRearmErrors,
              (unsigned)gHwaMissedFrameStarts,
              (unsigned)((gHwaDoneSeen ? 1U : 0U) |
                         (gHwaOutputSeen ? 2U : 0U)),
              (unsigned)gHwaFreezeRequests, (unsigned)gHwaFreezeCompletions,
              (unsigned)gHwaFreezeTimeouts, (unsigned)gHwaFreezeRestarts);
#endif
#else
    CLI_write("frames=%u wraps=%u active=%d calib=0x%x rf_faults=%u\n",
              (unsigned)gNumFrame, (unsigned)gNumWrap, (int)gCaptureActive,
              (unsigned)gCalibStatus, (unsigned)gRfFaults);
#endif
#endif
    return 0;
}

#ifdef ENABLE_HWA_SMOKE
/* CLI "hwastats": smoke-test visibility for the HWA driver/link path. */
static int32_t l3_cli_hwaStats(int32_t argc, char *argv[])
{
    (void)argc; (void)argv;
    CLI_write("hwa_opened=%u hwa_handle=0x%x hwa_open_err=%d "
              "test_err=%d peak_bin=%u peak_power=%u runs=%u "
              "real_err=%d real_peak_bin=%u real_peak_power=%u real_runs=%u\n",
              (unsigned)gHwaOpened, (unsigned)gHwaHandle, (int)gHwaOpenErr,
              (int)gHwaTestErr, (unsigned)gHwaTestPeakBin,
              (unsigned)gHwaTestPeakPower, (unsigned)gHwaTestRuns,
              (int)gHwaRealErr, (unsigned)gHwaRealPeakBin,
              (unsigned)gHwaRealPeakPower, (unsigned)gHwaRealRuns);
    return 0;
}

/* CLI "hwatest": software-triggered 128-point FFT on a synthetic tone.
 * This proves HWA param/common config and execution before live chirp plumbing. */
static int32_t l3_cli_hwaTest(int32_t argc, char *argv[])
{
    int16_t *src = (int16_t *)HWA_TEST_MEM0;
    uint32_t rx;
    int32_t errCode;

    (void)argc; (void)argv;
    gHwaTestRuns++;
    gHwaTestErr = 0;
    gHwaTestPeakBin = 0;
    gHwaTestPeakPower = 0;

    if (!gHwaOpened || gHwaHandle == NULL) {
        gHwaTestErr = -100;
        CLI_write("hwatest failed: HWA not open\n");
        return -1;
    }
    if (gCaptureActive) {
        gHwaTestErr = -101;
        CLI_write("hwatest failed: stop sensor first\n");
        return -1;
    }

    /* Build a sparse impulse without pulling in math libraries. This does not
     * validate frequency-bin placement yet; it proves the HWA FFT param set can
     * execute and produce non-zero output without touching live chirp timing. */
    memset((void *)src, 0, HWA_MEM_STRIDE);
    for (rx = 0; rx < HWA_FFT_RX; rx++) {
        uint32_t word = ((HWA_FFT_TONE_BIN * HWA_FFT_RX) + rx) * 2U;
        src[word + 0U] = 0;       /* imag */
        src[word + 1U] = 4096;    /* real */
    }

    errCode = l3_hwaRunFft(&gHwaTestPeakBin, &gHwaTestPeakPower);
    if (errCode != 0) {
        gHwaTestErr = errCode;
        CLI_write("hwatest failed: err=%d\n", errCode);
        return -1;
    }
    CLI_write("hwatest ok: peak_bin=%u peak_power=%u impulse_at_sample=%u runs=%u\n",
              (unsigned)gHwaTestPeakBin, (unsigned)gHwaTestPeakPower,
              (unsigned)HWA_FFT_TONE_BIN, (unsigned)gHwaTestRuns);
    return 0;
}

/* CLI "hwareal": copy the current ADCBUF chirp into HWA memory and FFT it.
 * This is not the final rolling path yet; it validates real chirp layout,
 * ADCBUF visibility, and HWA FFT together before event-driven snapshotting. */
static int32_t l3_cli_hwaReal(int32_t argc, char *argv[])
{
    volatile int16_t *adc = (volatile int16_t *)SOC_XWR68XX_MSS_ADCBUF_BASE_ADDRESS;
    int16_t *src = (int16_t *)HWA_TEST_MEM0;
    uint32_t sample, rx;
    int32_t errCode;

    (void)argc; (void)argv;
    gHwaRealRuns++;
    gHwaRealErr = 0;
    gHwaRealPeakBin = 0;
    gHwaRealPeakPower = 0;

    if (!gHwaOpened || gHwaHandle == NULL) {
        gHwaRealErr = -100;
        CLI_write("hwareal failed: HWA not open\n");
        return -1;
    }

    memset((void *)src, 0, HWA_MEM_STRIDE);
    for (sample = 0U; sample < HWA_FFT_SAMPLES; sample++) {
        for (rx = 0U; rx < HWA_FFT_RX; rx++) {
            uint32_t adcWord = ((rx * N_SAMPLES) + sample) * 2U;
            uint32_t hwaWord = ((sample * HWA_FFT_RX) + rx) * 2U;
            src[hwaWord + 0U] = adc[adcWord + 0U];
            src[hwaWord + 1U] = adc[adcWord + 1U];
        }
    }

    errCode = l3_hwaRunFft(&gHwaRealPeakBin, &gHwaRealPeakPower);
    if (errCode != 0) {
        gHwaRealErr = errCode;
        CLI_write("hwareal failed: err=%d\n", errCode);
        return -1;
    }
    CLI_write("hwareal ok: peak_bin=%u peak_power=%u active=%u frames=%u runs=%u\n",
              (unsigned)gHwaRealPeakBin, (unsigned)gHwaRealPeakPower,
              (unsigned)gCaptureActive, (unsigned)gNumFrame,
              (unsigned)gHwaRealRuns);
    return 0;
}
#endif

/* Configure the ADCBUF for our chirp format (complex, non-interleaved, 4 RX). */
static int32_t l3_configAdcBuf(void)
{
    ADCBuf_dataFormat dataFormat;
    ADCBuf_RxChanConf rxChanCfg;
    uint32_t          rxChanMask = 0xFU;
    uint32_t          chirpThreshold = 1U;
    uint8_t           ch;

    if (ADCBuf_control(gAdcbufHandle, ADCBufMMWave_CMD_CHANNEL_DISABLE,
                       (void *)&rxChanMask) != ADCBuf_STATUS_SUCCESS) {
        return -1;
    }
    memset((void *)&dataFormat, 0, sizeof(dataFormat));
    dataFormat.adcOutFormat      = 0;   /* complex */
    dataFormat.sampleInterleave  = 1;
    dataFormat.channelInterleave = 1;   /* non-interleaved: RX in contiguous blocks */
    if (ADCBuf_control(gAdcbufHandle, ADCBufMMWave_CMD_CONF_DATA_FORMAT,
                       (void *)&dataFormat) != ADCBuf_STATUS_SUCCESS) {
        return -1;
    }
    memset((void *)&rxChanCfg, 0, sizeof(rxChanCfg));
    for (ch = 0; ch < N_RX; ch++) {
        rxChanCfg.channel = ch;
        if (ADCBuf_control(gAdcbufHandle, ADCBufMMWave_CMD_CHANNEL_ENABLE,
                           (void *)&rxChanCfg) != ADCBuf_STATUS_SUCCESS) {
            return -1;
        }
        rxChanCfg.offset += N_SAMPLES * 2 * (uint32_t)sizeof(int16_t);
    }
    if (ADCBuf_control(gAdcbufHandle, ADCBufMMWave_CMD_SET_PING_CHIRP_THRESHHOLD,
                       (void *)&chirpThreshold) != ADCBuf_STATUS_SUCCESS) {
        return -1;
    }
    if (ADCBuf_control(gAdcbufHandle, ADCBufMMWave_CMD_SET_PONG_CHIRP_THRESHHOLD,
                       (void *)&chirpThreshold) != ADCBuf_STATUS_SUCCESS) {
        return -1;
    }
    return 0;
}

/* (Re)configure + enable the hardware-triggered capture EDMA. Exact clone of
 * the shipping demo's rangeproc dataIn (mmw_res.h + rangeProcHWA_ConfigEDMA_
 * DataIn, non-interleaved): DFE_CHIRP_AVAIL event, queue 0, SYNC_AB with
 * aCount=one RX chan (512 B), bCount=N_RX -- one event moves one whole chirp.
 * Source stays at the ADCBUF base every event (srcCIdx=0, hardware presents the
 * completed chirp there); dest advances one chirp per event through the ring
 * (their dstCIdx toggled between 2 HWA banks; ours walks RING_CHIRPS slots).
 * After RING_CHIRPS events the self-linked shadow reloads (dest back to ring
 * base) -> continuous rolling capture. */
static int32_t l3_armCapture(void)
{
#ifdef HWA_CHAINED_SNAPSHOT_RING
    return l3_armHwaChain();
#else
    EDMA_channelConfig_t   ch;
    EDMA_paramSetConfig_t *ps;
    EDMA_paramConfig_t     linkCfg;
#ifdef LIVE_SNAPSHOT_RING
    EDMA_paramConfig_t     linkCfgPong;
#endif
    uint32_t               srcAddr, dstAddr;

    srcAddr = SOC_translateAddress(
#ifdef LIVE_SNAPSHOT_RING
        SOC_XWR68XX_MSS_ADCBUF_BASE_ADDRESS,
#else
        SOC_XWR68XX_MSS_ADCBUF_BASE_ADDRESS +
            (SAVE_OFFSET_SAMPLES * 2U * (uint32_t)sizeof(int16_t)),
#endif
        SOC_TranslateAddr_Dir_TO_EDMA, NULL);
#ifdef LIVE_SNAPSHOT_RING
    dstAddr = SOC_translateAddress((uint32_t)&g_rawFrame[0][0],
                                   SOC_TranslateAddr_Dir_TO_EDMA, NULL);
#else
    dstAddr = SOC_translateAddress((uint32_t)&g_ring[0][0],
                                   SOC_TranslateAddr_Dir_TO_EDMA, NULL);
#endif

    (void)EDMA_disableChannel(gEdmaHandle, L3_EDMA_CHANNEL, EDMA3_CHANNEL_TYPE_DMA);

    memset((void *)&ch, 0, sizeof(ch));
    ch.channelId    = L3_EDMA_CHANNEL;
    ch.channelType  = (uint8_t)EDMA3_CHANNEL_TYPE_DMA;
    ch.paramId      = L3_EDMA_CHANNEL;
    ch.eventQueueId = 0U;                 /* demo EDMAIN_EVENT_QUE = 0 */
    ch.transferCompletionCallbackFxn    = l3_edmaCB;
    ch.transferCompletionCallbackFxnArg = (uintptr_t)0U;

    ps = &ch.paramSetConfig;
    ps->sourceAddress      = srcAddr;
    ps->destinationAddress = dstAddr;
#ifdef LIVE_SNAPSHOT_RING
    ps->aCount             = (uint16_t)(N_SAMPLES * 2U * sizeof(int16_t));
    ps->bCount             = (uint16_t)N_RX;
    ps->cCount             = (uint16_t)CHIRPS_PER_FRAME;
    ps->bCountReload       = (uint16_t)N_RX;
    ps->sourceBindex       = (int16_t)(N_SAMPLES * 2U * sizeof(int16_t));
    ps->destinationBindex  = (int16_t)(N_SAMPLES * 2U * sizeof(int16_t));
    ps->sourceCindex       = 0;
    ps->destinationCindex  = (int16_t)CHIRP_BYTES;
    ps->transferCompletionCode = (uint8_t)L3_EDMA_CHANNEL;
    ch.transferCompletionCallbackFxnArg = (uintptr_t)0U;
#else
    ps->aCount             = (uint16_t)(SAVE_SAMPLES * 2U * sizeof(int16_t));
    ps->bCount             = (uint16_t)N_RX;          /* 4 arrays = one chirp per event */
    ps->cCount             = (uint16_t)RING_CHIRPS;   /* events before wrap */
    ps->bCountReload       = (uint16_t)N_RX;
    ps->sourceBindex       = (int16_t)(N_SAMPLES * 2U * sizeof(int16_t)); /* step RX chans */
    ps->destinationBindex  = (int16_t)(SAVE_SAMPLES * 2U * sizeof(int16_t));
    ps->sourceCindex       = 0;                       /* re-read ADCBUF base every event */
    ps->destinationCindex  = (int16_t)SAVED_CHIRP_BYTES; /* advance dest one chirp per event */
    ps->transferCompletionCode = (uint8_t)L3_EDMA_CHANNEL;
#endif
    ps->linkAddress        = EDMA_NULL_LINK_ADDRESS;
    ps->transferType       = (uint8_t)EDMA3_SYNC_AB;
    ps->sourceAddressingMode      = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    ps->destinationAddressingMode = (uint8_t)EDMA3_ADDRESSING_MODE_LINEAR;
    ps->fifoWidth          = (uint8_t)EDMA3_FIFO_WIDTH_8BIT;
    ps->isStaticSet        = false;
    ps->isEarlyCompletion  = false;
    ps->isFinalTransferInterruptEnabled        = true;    /* per wrap */
    ps->isIntermediateTransferInterruptEnabled = false;
    ps->isFinalChainingEnabled        = false;
    ps->isIntermediateChainingEnabled = false;

    /* isEventTriggered = true: the DFE chirp event drives the channel. */
    if (EDMA_configChannel(gEdmaHandle, &ch, true) != EDMA_NO_ERROR) {
        return -1;
    }
    memcpy((void *)&linkCfg.paramSetConfig, (void *)ps, sizeof(EDMA_paramSetConfig_t));
    linkCfg.transferCompletionCallbackFxn    = l3_edmaCB;
#ifdef LIVE_SNAPSHOT_RING
    linkCfg.transferCompletionCallbackFxnArg = (uintptr_t)0U;
    linkCfg.paramSetConfig.destinationAddress =
        SOC_translateAddress((uint32_t)&g_rawFrame[0][0],
                             SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    memcpy((void *)&linkCfgPong, (void *)&linkCfg, sizeof(linkCfgPong));
    linkCfgPong.transferCompletionCallbackFxnArg = (uintptr_t)1U;
    linkCfgPong.paramSetConfig.destinationAddress =
        SOC_translateAddress((uint32_t)&g_rawFrame[1][0],
                             SOC_TranslateAddr_Dir_TO_EDMA, NULL);
    if (EDMA_configParamSet(gEdmaHandle, L3_EDMA_LINK_CHANNEL, &linkCfg) != EDMA_NO_ERROR) {
        return -1;
    }
    if (EDMA_configParamSet(gEdmaHandle, L3_EDMA_LINK_CHANNEL_PONG, &linkCfgPong) != EDMA_NO_ERROR) {
        return -1;
    }
    if (EDMA_linkParamSets(gEdmaHandle, L3_EDMA_CHANNEL, L3_EDMA_LINK_CHANNEL_PONG) != EDMA_NO_ERROR) {
        return -1;
    }
    if (EDMA_linkParamSets(gEdmaHandle, L3_EDMA_LINK_CHANNEL_PONG, L3_EDMA_LINK_CHANNEL) != EDMA_NO_ERROR) {
        return -1;
    }
    if (EDMA_linkParamSets(gEdmaHandle, L3_EDMA_LINK_CHANNEL, L3_EDMA_LINK_CHANNEL_PONG) != EDMA_NO_ERROR) {
        return -1;
    }
#else
    linkCfg.transferCompletionCallbackFxnArg = (uintptr_t)0U;
    if (EDMA_configParamSet(gEdmaHandle, L3_EDMA_LINK_CHANNEL, &linkCfg) != EDMA_NO_ERROR) {
        return -1;
    }
    if (EDMA_linkParamSets(gEdmaHandle, L3_EDMA_CHANNEL, L3_EDMA_LINK_CHANNEL) != EDMA_NO_ERROR) {
        return -1;
    }
    if (EDMA_linkParamSets(gEdmaHandle, L3_EDMA_LINK_CHANNEL, L3_EDMA_LINK_CHANNEL) != EDMA_NO_ERROR) {
        return -1;
    }
#endif
    /* Arm the channel to respond to the hardware event. */
    if (EDMA_enableChannel(gEdmaHandle, L3_EDMA_CHANNEL, EDMA3_CHANNEL_TYPE_DMA) != EDMA_NO_ERROR) {
        return -1;
    }
    return 0;
#endif
}

/* mmWave async-event callback: record RF health (calibration status, faults). */
static int32_t l3_mmwaveEvent(uint16_t msgId, uint16_t sbId, uint16_t sbLen,
                              uint8_t *payload)
{
    uint16_t asyncSB = RL_GET_SBID_FROM_UNIQ_SBID(sbId);
    (void)sbLen;

    if (msgId == RL_RF_ASYNC_EVENT_MSG) {
        switch (asyncSB) {
            case RL_RF_AE_INITCALIBSTATUS_SB:
                gCalibStatus = ((rlRfInitComplete_t *)payload)->calibStatus & 0x1FFFU;
                break;
            case RL_RF_AE_CPUFAULT_SB:
            case RL_RF_AE_ESMFAULT_SB:
            case RL_RF_AE_ANALOG_FAULT_SB:
                gRfFaults++;
                break;
            default:
                break;
        }
    }
    return 0;
}

#ifdef LIVE_SNAPSHOT_RING
static void l3_snapshotTask(UArg arg0, UArg arg1)
{
    (void)arg0; (void)arg1;

    while (1) {
        uintptr_t key;
        uint32_t  mask;
        uint32_t  slot;
        uint32_t  chirp;
        uint32_t  ringSlot;
        int32_t   err = 0;

        key = Hwi_disable();
        mask = gRawFrameReadyMask;
        if ((mask & 0x1U) != 0U) {
            slot = 0U;
            gRawFrameReadyMask &= ~0x1U;
        } else if ((mask & 0x2U) != 0U) {
            slot = 1U;
            gRawFrameReadyMask &= ~0x2U;
        } else {
            Hwi_restore(key);
            Task_sleep(1);
            continue;
        }
        Hwi_restore(key);

        gSnapshotBusy = 1U;
        ringSlot = gSnapshotFrames % RING_FRAMES;
        for (chirp = 0U; chirp < CHIRPS_PER_FRAME; chirp++) {
            const int16_t *rawChirp =
                &g_rawFrame[slot][chirp * N_RX * N_SAMPLES * 2U];
            int16_t *snapshotChirp =
                &g_ring[ringSlot][chirp * SNAPSHOT_CHIRP_COMPLEX * 2U];
            err = l3_snapshotChirpToBuffer(rawChirp, snapshotChirp);
            if (err != 0) {
                break;
            }
        }
        if (err == 0) {
            gSnapshotFrames++;
            gRingFrame = gSnapshotFrames;
            if ((gSnapshotFrames >= RING_FRAMES) &&
                ((gSnapshotFrames % RING_FRAMES) == 0U)) {
                gNumWrap++;
            }
        } else {
            gSnapshotErrors++;
        }
        gSnapshotBusy = 0U;
    }
}
#endif

/* mmWave control execution context (must outrank the CLI task). */
static void l3_mmwaveCtrlTask(UArg arg0, UArg arg1)
{
    int32_t errCode;
    (void)arg0; (void)arg1;
    while (1) {
        MMWave_execute(gMMWaveHandle, &errCode);
    }
}

/* CLI "sensorStart": open -> config -> ADCBUF + arm capture EDMA -> start. */
static int32_t l3_cli_sensorStart(int32_t argc, char *argv[])
{
    MMWave_ErrorLevel     errorLevel;
    int16_t               mmwErr, subErr;
    int32_t               errCode;
    (void)argc; (void)argv;

    if (!gSensorOpened) {
        /* Demo sends this before the first MMWave_open (board PA supply cfg);
         * values mirror the mmw demo's gRFLdoBypassCfg for this EVM class. */
        rlRfLdoBypassCfg_t ldoCfg;
        memset((void *)&ldoCfg, 0, sizeof(ldoCfg));
        if (rlRfSetLdoBypassConfig(RL_DEVICE_MAP_INTERNAL_BSS, &ldoCfg) != 0) {
            CLI_write("Error: rlRfSetLdoBypassConfig failed\n");
            return -1;
        }
        CLI_getMMWaveExtensionOpenConfig(&gOpenCfg);
        gOpenCfg.freqLimitLow                = 600U;
        gOpenCfg.freqLimitHigh               = 640U;
        gOpenCfg.calibMonTimeUnit            = 1;
        gOpenCfg.useCustomCalibration        = false;
        gOpenCfg.customCalibrationEnableMask = 0x0U;
        gOpenCfg.disableFrameStartAsyncEvent = false;
        gOpenCfg.disableFrameStopAsyncEvent  = false;
        if (MMWave_open(gMMWaveHandle, &gOpenCfg, NULL, &errCode) < 0) {
            MMWave_decodeError(errCode, &errorLevel, &mmwErr, &subErr);
            CLI_write("Error: MMWave_open failed [mmwave %d subsys %d]\n", mmwErr, subErr);
            return -1;
        }
        gSensorOpened = 1U;
    }

    CLI_getMMWaveExtensionConfig(&gCtrlCfg);
    /* Geometry guard: the ring/EDMA are compile-time sized, so a cfg with a
     * different loop count or sample count would capture garbage silently.
     * Refuse it loudly instead (three variant builds are in circulation). */
    {
        rlProfileCfg_t profCfg;
        memset((void *)&profCfg, 0, sizeof(profCfg));
        uint16_t chirpCount =
            (uint16_t)(gCtrlCfg.u.frameCfg.frameCfg.chirpEndIdx -
                       gCtrlCfg.u.frameCfg.frameCfg.chirpStartIdx + 1U);
        if ((gCtrlCfg.u.frameCfg.profileHandle[0] == NULL) ||
            (MMWave_getProfileCfg(gCtrlCfg.u.frameCfg.profileHandle[0],
                                  &profCfg, &errCode) < 0) ||
            (profCfg.numAdcSamples != N_SAMPLES) ||
#ifndef CONFIGURABLE_CAPTURE
            (gCtrlCfg.u.frameCfg.frameCfg.numLoops != LOOPS) ||
#endif
            (chirpCount != N_TX)) {
#ifdef CONFIGURABLE_CAPTURE
            CLI_write("Error: cfg geometry mismatch -- this firmware needs "
                      "%d TX x %d samples\n", N_TX, N_SAMPLES);
#else
            CLI_write("Error: cfg geometry mismatch — this firmware is built "
                      "for %d TX x %d samples x %d loops\n",
                      N_TX, N_SAMPLES, LOOPS);
#endif
            return -1;
        }
        /* framePeriodicity LSB = 5 ns -> microseconds. */
        gFramePeriodUs =
            (uint16_t)(gCtrlCfg.u.frameCfg.frameCfg.framePeriodicity / 200U);
#ifdef CONFIGURABLE_CAPTURE
        if (l3_finalizeCapturePlan(gCtrlCfg.u.frameCfg.frameCfg.numLoops) != 0) {
            return -1;
        }
#endif
    }
    if (MMWave_config(gMMWaveHandle, &gCtrlCfg, &errCode) < 0) {
        MMWave_decodeError(errCode, &errorLevel, &mmwErr, &subErr);
        CLI_write("Error: MMWave_config failed [mmwave %d subsys %d]\n", mmwErr, subErr);
        return -1;
    }

    if (l3_configAdcBuf() < 0) {
        CLI_write("Error: ADCBUF config failed\n");
        return -1;
    }
    gNumFrame  = 0U;
    gRingFrame = 0U;
    gNumWrap   = 0U;
#ifdef LIVE_SNAPSHOT_RING
    gRawFrameReadyMask = 0U;
    gRawFrameDrops     = 0U;
    gSnapshotFrames    = 0U;
    gSnapshotErrors    = 0U;
    gSnapshotBusy      = 0U;
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
    gHwaFrameDone      = 0U;
    gHwaOutputDone     = 0U;
    gHwaRearms         = 0U;
    gHwaRearmErrors    = 0U;
    gHwaMissedFrameStarts = 0U;
    gHwaArmedForFrame  = 0U;
    gHwaDoneSeen       = 0U;
    gHwaOutputSeen     = 0U;
    gHwaRearmPending   = 0U;
    gHwaRearmBusy      = 0U;
    gHwaFreezeRequested = 0U;
    gHwaShutdownRequested = 0U;
    gHwaFreezeRequestFrame = 0U;
    gHwaFreezeTargetFrame = 0U;
    gHwaFreezeRequests = 0U;
    gHwaFreezeCompletions = 0U;
    gHwaFreezeTimeouts = 0U;
    gHwaFreezeRestarts = 0U;
#ifdef CONFIGURABLE_CAPTURE
    gPreFramesCaptured = 0U;
    gPostFramesCaptured = 0U;
    gPostFramesObserved = 0U;
    gPostCaptureStarted = 0U;
    gActiveFrameIsPost = 0U;
    gActiveFrameShouldKeep = 1U;
#ifdef L3_RING_IQ8
    gIq8Pending = 0U;
    gIq8PendingSlot = 0U;
    gIq8PendingScratch = 0U;
    gIq8ActiveScratch = 0U;
    gIq8PackFrames = 0U;
    gIq8PackOverruns = 0U;
    gIq8ClippedComponents = 0U;
#ifdef L3_IQ8_EDMA_PACK
    gIq8EdmaBusy[0] = 0U;
    gIq8EdmaBusy[1] = 0U;
    gIq8EdmaDone = 0U;
    gIq8EdmaErrors = 0U;
    gIq8EdmaWaits = 0U;
    while (Semaphore_pend(gIq8EdmaDoneSemaphore, BIOS_NO_WAIT)) {
        /* Discard completion signals from the previous capture. */
    }
#endif
    memset((void *)gFrameIq8Scale, 0, sizeof(gFrameIq8Scale));
#endif
#endif
#endif
    if (l3_armCapture() < 0) {
        CLI_write("Error: capture arm failed\n");
        return -1;
    }
    gCaptureActive = 1U;

    if (l3_startFrontEnd() < 0) {
        gCaptureActive = 0U;
        CLI_write("Error: MMWave_start failed\n");
        return -1;
    }
    return 0;
}

static int32_t l3_cli_sensorStop(int32_t argc, char *argv[])
{
    (void)argc; (void)argv;
    if (!gCaptureActive) {
        return 0;
    }
#ifdef LIVE_SNAPSHOT_RING
    gRawFrameReadyMask = 0U;
#endif
    return l3_stopCaptureForShutdown();
}

/* System init task: UART, mmWave control, EDMA + ADCBUF + frame-start ISR, CLI. */
static void l3_initTask(UArg arg0, UArg arg1)
{
    MMWave_InitCfg        initCfg;
    UART_Params           uartParams;
    Task_Params           taskParams;
    ADCBuf_Params         adcbufParams;
    SOC_SysIntListenerCfg socIntCfg;
    EDMA_instanceInfo_t   edmaInstanceInfo;
#ifdef HWA_CHAINED_SNAPSHOT_RING
    Semaphore_Params      semaphoreParams;
#endif
    static CLI_Cfg        cliCfg;
    int32_t               errCode;

    (void)arg0; (void)arg1;

    UART_init();
    Pinmux_Set_FuncSel(SOC_XWR68XX_PINN5_PADBE, SOC_XWR68XX_PINN5_PADBE_MSS_UARTA_TX);
    Pinmux_Set_OverrideCtrl(SOC_XWR68XX_PINN5_PADBE,
                            PINMUX_OUTEN_RETAIN_HW_CTRL, PINMUX_INPEN_RETAIN_HW_CTRL);
    Pinmux_Set_FuncSel(SOC_XWR68XX_PINN4_PADBD, SOC_XWR68XX_PINN4_PADBD_MSS_UARTA_RX);
    Pinmux_Set_OverrideCtrl(SOC_XWR68XX_PINN4_PADBD,
                            PINMUX_OUTEN_RETAIN_HW_CTRL, PINMUX_INPEN_RETAIN_HW_CTRL);
    Pinmux_Set_OverrideCtrl(SOC_XWR68XX_PINF14_PADAJ,
                            PINMUX_OUTEN_RETAIN_HW_CTRL, PINMUX_INPEN_RETAIN_HW_CTRL);
    Pinmux_Set_FuncSel(SOC_XWR68XX_PINF14_PADAJ, SOC_XWR68XX_PINF14_PADAJ_MSS_UARTB_TX);

    UART_Params_init(&uartParams);
    uartParams.clockFrequency = gCpuClock;
    /* v3 UART-SPEED TEST: single-port operation. UARTA routes to the CP2105
     * ENHANCED interface (2 Mbps capable; the Standard iface that carried the
     * dump in v1/v2 is capped at 921600 AND threw sporadic -110 control
     * timeouts on the Pi). 1,041,667 divides EXACTLY on our side
     * (200 MHz/16/12) and to +0.17% in the CP2105 (24 MHz/23). CLI commands
     * and the dump share this port; the host syncs on the "ILD1" magic.
     * BINARY write mode is ESSENTIAL for the dump path (TEXT inserts \r
     * before 0x0A bytes); read mode stays TEXT for CLI line handling. */
    uartParams.writeDataMode  = UART_DATA_BINARY;
    uartParams.baudRate       = 1041667;
    uartParams.isPinMuxDone    = 1;
    gCliUart = UART_open(0, &uartParams);

    UART_Params_init(&uartParams);
    uartParams.clockFrequency = gCpuClock;
    /* BINARY write mode is ESSENTIAL: the driver default (UART_DATA_TEXT)
     * inserts \r before every 0x0A byte, corrupting a raw binary stream with
     * one-byte shifts (~1 insertion per 256 bytes). This -- not the EDMA, not
     * the baud -- was the capture-corruption root cause. */
    uartParams.writeDataMode  = UART_DATA_BINARY;
    uartParams.readDataMode   = UART_DATA_BINARY;
    /* 460800: divides cleanly from the 200 MHz UART clock (921600 does not:
     * divisor 13.56 -> 3-4% baud error). Keep the safe rate. */
    uartParams.baudRate       = 460800;
    uartParams.isPinMuxDone    = 1;
    gDataUart = UART_open(1, &uartParams);
    /* v3: dump goes out the CLI port (Enhanced iface) at 1.04 M. UARTB stays
     * open as a debug spare; flip this assignment back for v2 behavior. */
    gDataUart = gCliUart;

    /* EDMA + ADCBUF drivers. */
    EDMA_init(0);
    gEdmaHandle = EDMA_open(0, &errCode, &edmaInstanceInfo);
    if (gEdmaHandle == NULL) {
        return;
    }
    ADCBuf_init();
    ADCBuf_Params_init(&adcbufParams);
    adcbufParams.chirpThresholdPing = 1U;
    adcbufParams.chirpThresholdPong = 1U;
    adcbufParams.continousMode      = 0U;
    adcbufParams.socHandle          = gSocHandle;
    gAdcbufHandle = ADCBuf_open(0, &adcbufParams);
    if (gAdcbufHandle == NULL) {
        return;
    }

#ifdef ENABLE_HWA_SMOKE
    /* Incremental HWA bring-up: prove the driver/lib opens on MSS before we
     * put HWA in the chirp timing path. No HWA params or DMA triggers yet. */
    HWA_init();
    gHwaHandle = HWA_open(0, gSocHandle, &gHwaOpenErr);
    gHwaOpened = (gHwaHandle != NULL) ? 1U : 0U;
#endif

    /* Frame-start liveness ISR (per-chirp capture is now the hardware EDMA). */
    memset((void *)&socIntCfg, 0, sizeof(socIntCfg));
    socIntCfg.systemInterrupt = SOC_XWR68XX_MSS_FRAME_START_INT;
    socIntCfg.listenerFxn     = l3_frameStartISR;
    socIntCfg.arg             = (uintptr_t)0U;
    if (SOC_registerSysIntListener(gSocHandle, &socIntCfg, &errCode) == NULL) {
        return;
    }

    /* mmWave control (FULL, ISOLATION). */
    Mailbox_init(MAILBOX_TYPE_MSS);
    memset((void *)&initCfg, 0, sizeof(MMWave_InitCfg));
    initCfg.domain                  = MMWave_Domain_MSS;
    initCfg.socHandle               = gSocHandle;
    initCfg.eventFxn                = l3_mmwaveEvent;
    initCfg.linkCRCCfg.useCRCDriver = 1U;
    initCfg.linkCRCCfg.crcChannel   = CRC_Channel_CH1;
    initCfg.cfgMode                 = MMWave_ConfigurationMode_FULL;
    initCfg.executionMode           = MMWave_ExecutionMode_ISOLATION;
    gMMWaveHandle = MMWave_init(&initCfg, &errCode);
    if (gMMWaveHandle == NULL) {
        return;
    }
    while (1) {
        int32_t syncStatus = MMWave_sync(gMMWaveHandle, &errCode);
        if (syncStatus < 0) {
            return;
        }
        if (syncStatus == 1) {
            break;
        }
        Task_sleep(1);
    }
    Task_Params_init(&taskParams);
    taskParams.priority  = L3_CTRL_TASK_PRIORITY;
    taskParams.stackSize = 3 * 1024;
    Task_create(l3_mmwaveCtrlTask, &taskParams, NULL);

#ifdef LIVE_SNAPSHOT_RING
    Task_Params_init(&taskParams);
    taskParams.priority  = L3_SNAPSHOT_TASK_PRIORITY;
    taskParams.stackSize = 4 * 1024;
    Task_create(l3_snapshotTask, &taskParams, NULL);
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
    Semaphore_Params_init(&semaphoreParams);
    semaphoreParams.mode = Semaphore_Mode_BINARY;
    gHwaRearmSemaphore = Semaphore_create(0, &semaphoreParams, NULL);
    if (gHwaRearmSemaphore == NULL) {
        return;
    }
    gHwaFreezeSemaphore = Semaphore_create(0, &semaphoreParams, NULL);
    if (gHwaFreezeSemaphore == NULL) {
        return;
    }
#if defined(CONFIGURABLE_CAPTURE) && defined(L3_RING_IQ8) && \
    defined(L3_IQ8_EDMA_PACK)
    gIq8EdmaDoneSemaphore = Semaphore_create(0, &semaphoreParams, NULL);
    if (gIq8EdmaDoneSemaphore == NULL) {
        return;
    }
#endif
    Task_Params_init(&taskParams);
    taskParams.priority = L3_HWA_REARM_TASK_PRIORITY;
    taskParams.stackSize = 2U * 1024U;
    Task_create(l3_hwaRearmTask, &taskParams, NULL);
#endif

    /* CLI with the mmWave extension. */
    cliCfg.cliPrompt             = "l3dump:/>";
#ifdef HYBRID_CADENCE_CAPTURE
    cliCfg.cliBanner             = "OpenFlight hybrid-cadence L3 firmware (v6)\n";
#else
    cliCfg.cliBanner             = "OpenFlight configurable L3 snapshot firmware (v5)\n";
#endif
    cliCfg.cliUartHandle         = gCliUart;
    cliCfg.socHandle             = gSocHandle;
    cliCfg.mmWaveHandle          = gMMWaveHandle;
    cliCfg.enableMMWaveExtension = 1U;
    cliCfg.taskPriority          = L3_CLI_TASK_PRIORITY;
    cliCfg.usePolledMode         = true;
    cliCfg.tableEntry[0].cmd           = "sensorStart";
    cliCfg.tableEntry[0].helpString    = "Configure + start capture (no args)";
    cliCfg.tableEntry[0].cmdHandlerFxn = l3_cli_sensorStart;
    cliCfg.tableEntry[1].cmd           = "sensorStop";
    cliCfg.tableEntry[1].helpString    = "Stop the front-end";
    cliCfg.tableEntry[1].cmdHandlerFxn = l3_cli_sensorStop;
    cliCfg.tableEntry[2].cmd           = "l3dump";
    cliCfg.tableEntry[2].helpString    = "Freeze and stream one L3 snapshot dump";
    cliCfg.tableEntry[2].cmdHandlerFxn = l3_cli_dump;
    cliCfg.tableEntry[3].cmd           = "stats";
    cliCfg.tableEntry[3].helpString    = "Report capture counters";
    cliCfg.tableEntry[3].cmdHandlerFxn = l3_cli_stats;
#ifdef ENABLE_HWA_SMOKE
    cliCfg.tableEntry[4].cmd           = "hwastats";
    cliCfg.tableEntry[4].helpString    = "Report HWA smoke-test status";
    cliCfg.tableEntry[4].cmdHandlerFxn = l3_cli_hwaStats;
    cliCfg.tableEntry[5].cmd           = "hwatest";
    cliCfg.tableEntry[5].helpString    = "Run HWA FFT self-test";
    cliCfg.tableEntry[5].cmdHandlerFxn = l3_cli_hwaTest;
    cliCfg.tableEntry[6].cmd           = "hwareal";
    cliCfg.tableEntry[6].helpString    = "Run HWA FFT on current ADCBUF chirp";
    cliCfg.tableEntry[6].cmdHandlerFxn = l3_cli_hwaReal;
#endif
#ifdef CONFIGURABLE_CAPTURE
    cliCfg.tableEntry[7].cmd           = "captureCfg";
    cliCfg.tableEntry[7].helpString    =
        "captureCfg preStart preBins postStart postBins lateStart postFrames [postStride]";
    cliCfg.tableEntry[7].cmdHandlerFxn = l3_cli_captureCfg;
    cliCfg.tableEntry[8].cmd           = "phaseCaptureCfg";
    cliCfg.tableEntry[8].helpString    =
        "phaseCaptureCfg preStart preBins preFrames impactStart impactBins "
        "impactFrames postStart postBins lateStart ballFrames ballStride";
    cliCfg.tableEntry[8].cmdHandlerFxn = l3_cli_phaseCaptureCfg;
#ifdef L3_RING_IQ8
    cliCfg.tableEntry[9].cmd           = "captureFormat";
    cliCfg.tableEntry[9].helpString    = "captureFormat iq16|iq8";
    cliCfg.tableEntry[9].cmdHandlerFxn = l3_cli_captureFormat;
#ifdef L3_IQ8_EDMA_PACK
    cliCfg.tableEntry[10].cmd           = "iq8Scale";
    cliCfg.tableEntry[10].helpString    = "iq8Scale 16|32|64|128|256";
    cliCfg.tableEntry[10].cmdHandlerFxn = l3_cli_iq8Scale;
#endif
#endif
#endif
    CLI_open(&cliCfg);
}

int32_t main(void)
{
    Task_Params taskParams;
    SOC_Cfg     socCfg;
    int32_t     errCode;

    ESM_init(0U);
    memset((void *)&socCfg, 0, sizeof(SOC_Cfg));
    socCfg.clockCfg = SOC_SysClock_INIT;
    gSocHandle = SOC_init(&socCfg, &errCode);
    if (gSocHandle == NULL) {
        return -1;
    }
    Task_Params_init(&taskParams);
    taskParams.priority  = L3_INIT_TASK_PRIORITY;
    taskParams.stackSize = 8 * 1024;
    Task_create(l3_initTask, &taskParams, NULL);
    BIOS_start();
    return 0;
}
