/**
 * Lightweight Socket.IO mock backend for UI development without Python/hardware.
 *
 * Listens on port 8080 — the same origin Vite's serverOrigin heuristic expects.
 */

import express from 'express';
import { createServer } from 'node:http';
import { Server } from 'socket.io';
import { registerHandlers } from './handlers.js';
import { MockSession } from './session.js';

const PORT = Number(process.env.MOCK_PORT ?? 8080);
const MOCK_REPLAY_MP4 = Buffer.from(
  'AAAAJGZ0eXBpc29tAAACAGlzb21pc282aXNvMmF2YzFtcDQxAAAC5W1vb3YAAABsbXZoZAAAAAAAAAAAAAAAAAAAA+gAAAAAAAEAAAEAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAHndHJhawAAAFx0a2hkAAAAAwAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAQAAAAEAAAAAABg21kaWEAAAAgbWRoZAAAAAAAAAAAAAAAAAAAKAAAAAAAVcQAAAAAAC1oZGxyAAAAAAAAAAB2aWRlAAAAAAAAAAAAAAAAVmlkZW9IYW5kbGVyAAAAAS5taW5mAAAAFHZtaGQAAAABAAAAAAAAAAAAAAAkZGluZgAAABxkcmVmAAAAAAAAAAEAAAAMdXJsIAAAAAEAAADuc3RibAAAAKJzdHNkAAAAAAAAAAEAAACSYXZjMQAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAQABAASAAAAEgAAAAAAAAAARVMYXZjNjIuMjguMTAxIGxpYngyNjQAAAAAAAAAAAAAABj//wAAACxhdmNDAULACv/hABVnQsAK2nsBEAAAAwAQAAADAKDxImoBAARozgRyAAAAEHBhc3AAAAABAAAAAQAAABBzdHRzAAAAAAAAAAAAAAAQc3RzYwAAAAAAAAAAAAAAFHN0c3oAAAAAAAAAAAAAAAAAAAAQc3RjbwAAAAAAAAAAAAAAKG12ZXgAAAAgdHJleAAAAAAAAAABAAAAAQAAAAAAAAAAAAAAAAAAAGJ1ZHRhAAAAWm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALWlsc3QAAAAlqXRvbwAAAB1kYXRhAAAAAQAAAABMYXZmNjIuMTIuMTAxAAAAcG1vb2YAAAAQbWZoZAAAAAAAAAABAAAAWHRyYWYAAAAkdGZoZAAAADkAAAABAAAAAAAAAwkAAAgAAAACZQEBAAAAAAAUdGZkdAEAAAAAAAAAAAAAAAAAABh0cnVuAAAABQAAAAEAAAB4AgAAAAAAAm1tZGF0AAACUwYF//9P3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NSByMzIyMiBiMzU2MDVhIC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTAgcmVmPTEgZGVibG9jaz0wOjA6MCBhbmFseXNpPTA6MCBtZT1kaWEgc3VibWU9MCBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0wIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MCA4eDhkY3Q9MCBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0wIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTAgd2VpZ2h0cD0wIGtleWludD0yNTAga2V5aW50X21pbj01IHNjZW5lY3V0PTAgaW50cmFfcmVmcmVzaD0wIHJjPWNyZiBtYnRyZWU9MCBjcmY9MzAuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0wAIAAAAAKZYiEOiYoAAhv4AAAAENtZnJhAAAAK3RmcmEBAAAAAAAAAQAAAAAAAAABAAAAAAAAAAAAAAAAAAADCQEBAQAAABBtZnJvAAAAAAAAAEM=',
  'base64'
);

const app = express();
app.use(express.json());

app.post('/api/shutdown', (_req, res) => {
  // UI expects 200; do not exit so the mock stays up during UI work.
  res.json({ status: 'shutting_down' });
});

app.post('/api/camera/replays/:replayId/prepare', (req, res) => {
  const replayId = String(req.params.replayId);
  res.json({
    id: replayId,
    frame_count: 99,
    trigger_frame: 73,
    playback_fps: 60,
    duration_seconds: 1.65,
    display_mirror_horizontal: true,
    video_url: `/api/camera/replays/${encodeURIComponent(replayId)}/video`,
  });
});

app.get('/api/camera/replays/:replayId/video', (_req, res) => {
  res.status(200).type('video/mp4').send(MOCK_REPLAY_MP4);
});

app.get('/health', (_req, res) => {
  res.json({ ok: true, mock: true });
});

const httpServer = createServer(app);
const io = new Server(httpServer, {
  cors: { origin: '*' },
});

const session = new MockSession();
registerHandlers(io, session);

httpServer.listen(PORT, () => {
  console.log(`[mock-server] listening on http://localhost:${PORT}`);
  console.log('[mock-server] connect the Vite UI (port 5173) — Simulate generates shots');
});
