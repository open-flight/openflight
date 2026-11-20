/* See l3_impact_fit.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_impact_fit.h"
#include "l3_text.h"

void l3_impact_fit_cfg_defaults(l3_impact_fit_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->binWidthM = 6.0F / 128.0F;
    cfg->bandBins = 6.0F;    /* the ridge on the 2026-09-28 capture; 0 turns it off */
    cfg->fitPoints = 4U;     /* about 12 ms at 3 ms frames */
    cfg->minPoints = 3U;     /* a line and a residual */
    cfg->clubMinMps = 17.0F; /* over a late backswing's downrange crossing; downswings are 30-50 */
    cfg->clubMaxMps = 70.0F;
    cfg->clubOutMaxRatio = 1.10F; /* after impact the club only slows */
    cfg->ballMinMps = 15.0F;
    cfg->ballMaxMps = 90.0F;
    cfg->gateSigmas = 3.0F;
    cfg->minSigmaUs = 500.0F;
    cfg->maxSigmaUs = 3000.0F; /* one 3 ms frame */
    cfg->bandSearchBins = 10.0F;
    cfg->clutterSigmas = 0.0F; /* off until the labelled replays settle it */
    /* 4 points span 9 ms at 3 ms frames; at 2 ms the same span takes 6. Under
     * 9 ms so a 3 ms frame's few-us jitter never adds a fifth point. */
    cfg->fitSpanUs = 8500U;
}

void l3_impact_fit_reset(l3_impact_fit_t *fit)
{
    uint32_t i;

    memset(fit, 0, sizeof(*fit));
    for (i = 0U; i < L3_FIT_TRACKS; i++)
    {
        fit->track[i].why = L3_FIT_WHY_MISSING;
    }
    fit->droppedTrack = L3_FIT_NO_TRACK;
}

int32_t l3_fit_list_point(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    const l3_fit_list_t *list = (const l3_fit_list_t *)ctx;

    if (index >= list->count)
    {
        return 0;
    }
    *out = list->points[index];
    return 1;
}

int32_t l3_fit_span_point(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    const l3_fit_span_t *span = (const l3_fit_span_t *)ctx;

    if (index >= span->count)
    {
        return 0;
    }
    return l3_track_point(span->track, span->first + index, out);
}

void l3_fit_span_after(const l3_club_track_t *track, uint32_t afterFrame, l3_fit_span_t *out)
{
    l3_track_point_t point;
    uint32_t i;

    /* A newest point still tentative (l3_track_follow) is not yet the club's. */
    uint32_t held = (track->tentative && track->count > 0U) ? track->count - 1U : track->count;

    out->track = track;
    out->first = held;
    out->count = 0U;
    for (i = 0U; i < held; i++)
    {
        (void)l3_track_point(track, i, &point);
        if (point.frame > afterFrame)
        {
            out->first = i;
            out->count = held - i;
            return;
        }
    }
}

/* This track's speed bounds; club out has no floor beyond moving downrange. */
static void l3_fit_bounds(const l3_impact_fit_cfg_t *cfg, uint8_t which, float *lo, float *hi)
{
    if (which == L3_FIT_BALL_OUT)
    {
        *lo = cfg->ballMinMps;
        *hi = cfg->ballMaxMps;
    }
    else if (which == L3_FIT_CLUB_IN)
    {
        *lo = cfg->clubMinMps;
        *hi = cfg->clubMaxMps;
    }
    else
    {
        *lo = 0.0F;
        *hi = cfg->clubMaxMps;
    }
}

void l3_impact_fit_track(const l3_impact_fit_cfg_t *cfg, uint8_t which, l3_point_at_fn pointAt,
                         const void *ctx, uint32_t count, float ballRangeM,
                         l3_fit_estimate_t *out)
{
    float t[L3_FIT_MAX_POINTS];
    float r[L3_FIT_MAX_POINTS];
    uint32_t want = (cfg->fitPoints < L3_FIT_MAX_POINTS) ? cfg->fitPoints : L3_FIT_MAX_POINTS;
    uint32_t n;
    uint32_t first;
    uint32_t i;
    uint32_t firstUs;
    float tMean = 0.0F;
    float rMean = 0.0F;
    float stt = 0.0F;
    float str = 0.0F;
    float rss = 0.0F;
    float v;
    float lo;
    float hi;
    float tk;
    float se;
    float floorM;
    l3_track_point_t point;

    memset(out, 0, sizeof(*out));
    out->why = L3_FIT_WHY_MISSING;
    if (pointAt == NULL || count == 0U)
    {
        return;
    }
    n = (count < want) ? count : want;
    /* Club in: older points until the fit spans fitSpanUs (l3_impact_fit_cfg_t). */
    while (which == L3_FIT_CLUB_IN && cfg->fitSpanUs > 0U && n >= 2U && n < count &&
           n < L3_FIT_MAX_POINTS)
    {
        l3_track_point_t a;
        l3_track_point_t b;
        uint32_t lo = count - n;

        if (pointAt(ctx, lo, &a) == 0 || pointAt(ctx, lo + n - 1U, &b) == 0 ||
            (uint32_t)(b.timestampUs - a.timestampUs) >= cfg->fitSpanUs)
        {
            break;
        }
        n++;
    }
    first = (which == L3_FIT_CLUB_IN) ? count - n : 0U;
    out->points = n;
    if (n < 3U || n < cfg->minPoints)
    {
        out->why = L3_FIT_WHY_FEW_POINTS;
        return;
    }
    if (pointAt(ctx, first, &point) == 0)
    {
        out->why = L3_FIT_WHY_MISSING;
        return;
    }
    /* Every dt is an integer difference from the first point, wrap-safe at
     * the uint32 us rollover and exact before the float conversion. */
    firstUs = point.timestampUs;
    for (i = 0U; i < n; i++)
    {
        if (pointAt(ctx, first + i, &point) == 0)
        {
            out->why = L3_FIT_WHY_MISSING;
            return;
        }
        t[i] = (float)(int32_t)(point.timestampUs - firstUs) * 1.0e-6F;
        r[i] = point.rangeM;
        tMean += t[i];
        rMean += r[i];
    }
    tMean /= (float)n;
    rMean /= (float)n;
    for (i = 0U; i < n; i++)
    {
        float dt = t[i] - tMean;

        stt += dt * dt;
        str += dt * (r[i] - rMean);
    }
    if (!(stt > 0.0F))
    {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    v = str / stt;
    for (i = 0U; i < n; i++)
    {
        float e = r[i] - (rMean + v * (t[i] - tMean));

        rss += e * e;
    }
    out->speedMps = v;
    if (!isfinite(v) || !isfinite(rss))
    {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    if (!(v > 0.0F))
    {
        out->why = L3_FIT_WHY_WRONG_DIRECTION;
        return;
    }
    l3_fit_bounds(cfg, which, &lo, &hi);
    if (v < lo || v > hi)
    {
        out->why = L3_FIT_WHY_SPEED_BOUNDS;
        return;
    }
    tk = tMean + (ballRangeM - rMean) / v;
    se = sqrtf(rss / (float)(n - 2U)) *
         sqrtf(1.0F / (float)n + (tk - tMean) * (tk - tMean) / stt);
    floorM = cfg->binWidthM / sqrtf(12.0F);
    if (se < floorM)
    {
        se = floorM;
    }
    /* The crossing is solved relative to the first point; adding the integer
     * base back stores an absolute time whose float step is 2^-23 of it (128 us
     * at 1.8e9 us, about 30 minutes of uptime). Past the uint32 wrap it can
     * exceed 2^32; l3_round_us folds it back. */
    out->timeUs = (float)firstUs + tk * 1.0e6F;
    out->sigmaUs = se / v * 1.0e6F;
    if (!isfinite(out->timeUs) || !isfinite(out->sigmaUs))
    {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    if (cfg->maxSigmaUs > 0.0F && out->sigmaUs > cfg->maxSigmaUs)
    {
        /* Time, sigma and speed stay filled for diagnostics. */
        out->why = L3_FIT_WHY_UNCERTAIN;
        return;
    }
    out->why = L3_FIT_WHY_OK;
}

static const char *const kWhyNames[L3_FIT_WHY_COUNT] = {
    "ok", "missing", "few_points", "wrong_direction", "speed_bounds", "physics", "nonfinite",
    "dropped", "uncertain"};
static const char *const kVerdictNames[L3_FIT_VERDICT_COUNT] = {
    "none", "single_track", "consistent", "inconsistent"};
static const char *const kTrackNames[L3_FIT_TRACKS] = {"club_in", "club_out", "ball_out"};

static int32_t l3_fit_ok(const l3_fit_estimate_t *e)
{
    return (e->why == L3_FIT_WHY_OK) ? 1 : 0;
}

/* Inverse-variance mean of the kept estimates other than skip (L3_FIT_NO_TRACK
 * skips none); returns how many were used. */
static uint32_t l3_fit_mean(const l3_impact_fit_t *fit, uint8_t skip, float *mean)
{
    float weights = 0.0F;
    float sum = 0.0F;
    uint32_t used = 0U;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++)
    {
        const l3_fit_estimate_t *e = &fit->track[i];
        float w;

        if (i == (uint32_t)skip || !l3_fit_ok(e))
        {
            continue;
        }
        w = 1.0F / (e->sigmaUs * e->sigmaUs);
        weights += w;
        sum += w * e->timeUs;
        used++;
    }
    *mean = (used > 0U) ? sum / weights : 0.0F;
    return used;
}

/* How far one estimate lies from mean in units of its gate,
 * gateSigmas * max(sigma, minSigmaUs); it agrees when this is at most 1. */
static float l3_fit_gate_ratio(const l3_impact_fit_cfg_t *cfg, const l3_fit_estimate_t *e,
                               float mean)
{
    float sigma = (e->sigmaUs > cfg->minSigmaUs) ? e->sigmaUs : cfg->minSigmaUs;

    return fabsf(e->timeUs - mean) / (cfg->gateSigmas * sigma);
}

/* The largest gate ratio of the kept estimates other than skip around mean:
 * the group agrees when it is at most 1. */
static float l3_fit_disagreement(const l3_impact_fit_cfg_t *cfg, const l3_impact_fit_t *fit,
                                 uint8_t skip, float mean)
{
    float worst = 0.0F;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++)
    {
        float ratio;

        if (i == (uint32_t)skip || !l3_fit_ok(&fit->track[i]))
        {
            continue;
        }
        ratio = l3_fit_gate_ratio(cfg, &fit->track[i], mean);
        if (ratio > worst)
        {
            worst = ratio;
        }
    }
    return worst;
}

/* Three kept estimates that do not all agree around their mean: the track to
 * drop, or L3_FIT_NO_TRACK when no pair justifies dropping one. Each
 * leave-one-out pair is judged around its own inverse-variance mean, so a
 * sharp outlier cannot drag the mean onto itself and push the good tracks
 * out of their gates. Among the pairs that agree the one with the smallest
 * disagreement wins; ties go to the pair leaving out the earlier track
 * (club_in, then club_out, then ball_out). The left-out track is dropped only
 * if it fails its gate around the winning pair's mean. */
static uint8_t l3_fit_pick_drop(const l3_impact_fit_cfg_t *cfg, const l3_impact_fit_t *fit,
                                float *pairMean)
{
    uint8_t best = L3_FIT_NO_TRACK;
    float bestRatio = 0.0F;
    float bestMean = 0.0F;
    uint8_t k;

    for (k = 0U; k < (uint8_t)L3_FIT_TRACKS; k++)
    {
        float mean;
        float ratio;

        (void)l3_fit_mean(fit, k, &mean);
        ratio = l3_fit_disagreement(cfg, fit, k, mean);
        if (ratio <= 1.0F && (best == L3_FIT_NO_TRACK || ratio < bestRatio))
        {
            best = k;
            bestRatio = ratio;
            bestMean = mean;
        }
    }
    if (best == L3_FIT_NO_TRACK || l3_fit_gate_ratio(cfg, &fit->track[best], bestMean) <= 1.0F)
    {
        return L3_FIT_NO_TRACK;
    }
    *pairMean = bestMean;
    return best;
}

static float l3_fit_sharpest(const l3_impact_fit_t *fit)
{
    const l3_fit_estimate_t *best = NULL;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++)
    {
        const l3_fit_estimate_t *e = &fit->track[i];

        if (l3_fit_ok(e) && (best == NULL || e->sigmaUs < best->sigmaUs))
        {
            best = e;
        }
    }
    return (best != NULL) ? best->timeUs : 0.0F;
}

static float l3_fit_spread(const l3_impact_fit_t *fit)
{
    float lo = 0.0F;
    float hi = 0.0F;
    uint32_t seen = 0U;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++)
    {
        const l3_fit_estimate_t *e = &fit->track[i];

        if (!l3_fit_ok(e))
        {
            continue;
        }
        if (seen == 0U || e->timeUs < lo)
        {
            lo = e->timeUs;
        }
        if (seen == 0U || e->timeUs > hi)
        {
            hi = e->timeUs;
        }
        seen++;
    }
    return hi - lo;
}

void l3_impact_fit_solve(const l3_impact_fit_cfg_t *cfg, l3_impact_fit_t *fit, uint32_t triggerUs)
{
    l3_fit_estimate_t *in = &fit->track[L3_FIT_CLUB_IN];
    l3_fit_estimate_t *co = &fit->track[L3_FIT_CLUB_OUT];
    l3_fit_estimate_t *bo = &fit->track[L3_FIT_BALL_OUT];
    uint32_t used;
    uint8_t drop;
    float mean;

    fit->verdict = L3_FIT_VERDICT_NONE;
    fit->droppedTrack = L3_FIT_NO_TRACK;
    fit->impactUs = 0.0F;
    fit->spreadUs = 0.0F;
    fit->refinedMinusTriggerUs = 0.0F;
    if (l3_fit_ok(in) && l3_fit_ok(co) && co->speedMps > in->speedMps * cfg->clubOutMaxRatio)
    {
        co->why = L3_FIT_WHY_PHYSICS;
    }
    if (l3_fit_ok(co) && l3_fit_ok(bo) && bo->speedMps <= co->speedMps)
    {
        bo->why = L3_FIT_WHY_PHYSICS;
    }
    used = l3_fit_mean(fit, L3_FIT_NO_TRACK, &mean);
    if (used == 0U)
    {
        return;
    }
    if (used == 1U || l3_fit_disagreement(cfg, fit, L3_FIT_NO_TRACK, mean) <= 1.0F)
    {
        fit->verdict = (used == 1U) ? L3_FIT_VERDICT_SINGLE : L3_FIT_VERDICT_CONSISTENT;
        fit->impactUs = mean;
    }
    else
    {
        drop = (used == 3U) ? l3_fit_pick_drop(cfg, fit, &mean) : L3_FIT_NO_TRACK;
        if (drop != L3_FIT_NO_TRACK)
        {
            fit->track[drop].why = L3_FIT_WHY_DROPPED;
            fit->droppedTrack = drop;
            fit->verdict = L3_FIT_VERDICT_CONSISTENT;
            fit->impactUs = mean;
        }
        else
        {
            fit->verdict = L3_FIT_VERDICT_INCONSISTENT;
            fit->impactUs = l3_fit_sharpest(fit);
        }
    }
    fit->spreadUs = l3_fit_spread(fit);
    fit->refinedMinusTriggerUs = fit->impactUs - (float)triggerUs;
}

void l3_impact_fit_run(const l3_impact_fit_cfg_t *cfg, const l3_fit_list_t *clubIn,
                       const l3_fit_span_t *clubOut, const l3_fit_span_t *ballOut,
                       float ballRangeM, uint8_t noLock, uint32_t triggerUs,
                       l3_impact_fit_t *fit)
{
    l3_impact_fit_reset(fit);
    fit->noLock = noLock;
    if (clubIn != NULL)
    {
        l3_impact_fit_track(cfg, L3_FIT_CLUB_IN, l3_fit_list_point, clubIn, clubIn->count,
                            ballRangeM, &fit->track[L3_FIT_CLUB_IN]);
    }
    if (clubOut != NULL)
    {
        l3_impact_fit_track(cfg, L3_FIT_CLUB_OUT, l3_fit_span_point, clubOut, clubOut->count,
                            ballRangeM, &fit->track[L3_FIT_CLUB_OUT]);
    }
    if (ballOut != NULL)
    {
        l3_impact_fit_track(cfg, L3_FIT_BALL_OUT, l3_fit_span_point, ballOut, ballOut->count,
                            ballRangeM, &fit->track[L3_FIT_BALL_OUT]);
    }
    l3_impact_fit_solve(cfg, fit, triggerUs);
}

uint32_t l3_round_us(float us)
{
    uint32_t whole;

    if (!isfinite(us) || !(us > 0.0F) || us >= 8589934592.0F)
    {
        return 0U;
    }
    if (us >= 4294967296.0F)
    {
        us -= 4294967296.0F;
    }
    whole = (uint32_t)us;
    return (us - (float)whole >= 0.5F) ? whole + 1U : whole;
}

/* A float rounded half away from zero to an int, clamped to +-INT32_MAX
 * (a huge finite float converted to int is undefined). */
static int l3_fit_int(float v)
{
    if (!isfinite(v))
    {
        return (v > 0.0F) ? INT32_MAX : ((v < 0.0F) ? -INT32_MAX : 0);
    }
    if (v >= 2147483648.0F)
    {
        return INT32_MAX;
    }
    if (v <= -2147483648.0F)
    {
        return -INT32_MAX;
    }
    return (int)((v >= 0.0F) ? v + 0.5F : v - 0.5F);
}

const char *l3_impact_fit_why_name(uint8_t why)
{
    return (why < L3_FIT_WHY_COUNT) ? kWhyNames[why] : "?";
}

const char *l3_impact_fit_verdict_name(uint8_t verdict)
{
    return (verdict < L3_FIT_VERDICT_COUNT) ? kVerdictNames[verdict] : "?";
}

int32_t l3_impact_fit_format(const l3_impact_fit_t *fit, char *out, uint32_t cap)
{
    char tracks[L3_FIT_TRACKS][48];
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++)
    {
        const l3_fit_estimate_t *e = &fit->track[i];

        if (e->why == L3_FIT_WHY_OK || e->why == L3_FIT_WHY_DROPPED ||
            e->why == L3_FIT_WHY_UNCERTAIN)
        {
            (void)snprintf(tracks[i], sizeof(tracks[i]), "%s=%s:%u+-%d", kTrackNames[i],
                           l3_impact_fit_why_name(e->why), (unsigned)l3_round_us(e->timeUs),
                           l3_fit_int(e->sigmaUs));
        }
        else
        {
            (void)snprintf(tracks[i], sizeof(tracks[i]), "%s=%s", kTrackNames[i],
                           l3_impact_fit_why_name(e->why));
        }
    }
    return snprintf(out, cap,
                    "impactfit verdict=%s t=%u spreadus=%d dtrigus=%d dropped=%s nolock=%u "
                    "%s %s %s",
                    l3_impact_fit_verdict_name(fit->verdict), (unsigned)l3_round_us(fit->impactUs),
                    l3_fit_int(fit->spreadUs), l3_fit_int(fit->refinedMinusTriggerUs),
                    (fit->droppedTrack < L3_FIT_TRACKS) ? kTrackNames[fit->droppedTrack] : "-",
                    (unsigned)fit->noLock, tracks[0], tracks[1], tracks[2]);
}
