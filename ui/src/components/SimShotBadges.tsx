import './SimShotBadges.css';
import type { SimShotInfo } from '../types/socket';

const DISPLAY_NAMES: Record<string, string> = {
  gspro: 'GSPro',
  opengolfsim: 'OpenGolfSim',
  partee: 'PAR-TEE',
};

// Human labels for the logical field keys shared by all connectors.
const FIELD_LABELS: Record<string, string> = {
  ball_speed: 'Ball',
  vla: 'VLA',
  hla: 'HLA',
  total_spin: 'Spin',
  spin_axis: 'Axis',
  back_spin: 'Backspin',
  side_spin: 'Sidespin',
  carry: 'Carry',
  club_speed: 'Club',
  club_path: 'Path',
};

function formatValue(value: number | null): string {
  if (value === null || value === undefined) return '—';
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

interface SimShotBadgesProps {
  latestSimShots: Record<string, SimShotInfo>;
}

export function SimShotBadges({ latestSimShots }: SimShotBadgesProps) {
  const sims = Object.values(latestSimShots);
  if (sims.length === 0) {
    return null;
  }
  return (
    <div className="sim-shot-badges">
      {sims.map((sim) => {
        const fields = sim.fields.filter((f) => f in sim.values);
        const measured = fields.filter((f) => sim.provenance[f] === 'measured').length;
        return (
          <div key={sim.target} className="sim-shot-badges__card">
            <div className="sim-shot-badges__header">
              <span className="sim-shot-badges__title">{`Sent to ${DISPLAY_NAMES[sim.target] ?? sim.target}`}</span>
              <span className="sim-shot-badges__shot">{`#${sim.shot_number}`}</span>
            </div>
            <div className="sim-shot-badges__grid">
              {fields.map((f) => {
                const prov = sim.provenance[f] ?? 'estimated';
                return (
                  <div key={f} className="sim-shot-badges__item">
                    <span className="sim-shot-badges__label">{FIELD_LABELS[f] ?? f}</span>
                    <span className="sim-shot-badges__value">{formatValue(sim.values[f])}</span>
                    <span className={`sim-shot-badges__badge sim-shot-badges__badge--${prov}`} title={prov}>
                      {prov === 'measured' ? 'M' : 'E'}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="sim-shot-badges__summary">
              {`${measured} measured / ${fields.length - measured} estimated`}
            </div>
          </div>
        );
      })}
    </div>
  );
}
