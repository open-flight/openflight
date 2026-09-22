import { create } from 'zustand';
import type { PowerStatus } from '../types/power';
import type { SimShotInfo, SimStatus } from '../types/socket';

interface SystemState {
  connected: boolean;
  mockMode: boolean;
  debugMode: boolean;
  cloudUploadState: 'idle' | 'running' | 'complete' | 'error';
  cloudUploadMessage: string;
  simStatuses: Record<string, SimStatus>;
  latestSimShots: Record<string, SimShotInfo>;
  serverClub: string | null;
  powerStatus: PowerStatus | null;
  setConnected: (connected: boolean) => void;
  setMockMode: (mockMode: boolean) => void;
  setDebugMode: (debugMode: boolean) => void;
  setCloudUploadStatus: (state: SystemState['cloudUploadState'], message: string) => void;
  setSimStatus: (status: SimStatus) => void;
  setLatestSimShot: (shot: SimShotInfo) => void;
  setServerClub: (club: string | null) => void;
  setPowerStatus: (status: PowerStatus) => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  connected: false,
  mockMode: false,
  debugMode: false,
  cloudUploadState: 'idle',
  cloudUploadMessage: '',
  simStatuses: {},
  latestSimShots: {},
  serverClub: null,
  powerStatus: null,
  setConnected: (connected) => set({ connected }),
  setMockMode: (mockMode) => set({ mockMode }),
  setDebugMode: (debugMode) => set({ debugMode }),
  setCloudUploadStatus: (cloudUploadState, cloudUploadMessage) => set({ cloudUploadState, cloudUploadMessage }),
  setSimStatus: (status) =>
    set((state) => ({
      simStatuses: { ...state.simStatuses, [status.target]: status },
    })),
  setLatestSimShot: (shot) =>
    set((state) => ({
      latestSimShots: { ...state.latestSimShots, [shot.target]: shot },
    })),
  setServerClub: (serverClub) => set({ serverClub }),
  setPowerStatus: (status) => set({ powerStatus: status }),
}));
