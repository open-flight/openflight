import type { ReactNode } from 'react';
import type { ShotProcessingPhase } from '../stores/useShotStore';
import { ProgressIndicator } from './ProgressIndicator';
import './ShotProcessingArea.css';

interface ShotProcessingAreaProps {
  phase: ShotProcessingPhase | null;
  children: ReactNode;
}

const ENRICHMENT_DETAILS: Partial<Record<ShotProcessingPhase, string>> = {
  iwr_dump: 'Receiving IWR radar dump…',
  camera_processing: 'Processing camera capture…',
  hardware_enrichment: 'Receiving IWR radar dump and processing camera capture…',
};

export function ShotProcessingArea({ phase, children }: ShotProcessingAreaProps) {
  const enrichmentDetail = phase ? ENRICHMENT_DETAILS[phase] : undefined;

  if (enrichmentDetail) {
    return (
      <>
        <div className="shot-processing-status">
          <ProgressIndicator variant="inline" title="OPS metrics ready" detail={enrichmentDetail} />
        </div>
        {children}
      </>
    );
  }

  return (
    <>
      {phase ? (
        <div className="shot-processing-overlay">
          <div className="shot-processing-card">
            <ProgressIndicator
              variant="dialog"
              title={phase === 'capturing' ? 'Impact detected' : 'Shot captured'}
              detail={phase === 'capturing' ? 'Capturing radar data…' : 'Calculating metrics…'}
            />
          </div>
        </div>
      ) : null}
      {children}
    </>
  );
}
