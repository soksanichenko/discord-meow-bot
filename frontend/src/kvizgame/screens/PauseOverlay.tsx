interface PauseOverlayProps {
  isHost: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function PauseOverlay({ isHost, send }: PauseOverlayProps) {
  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 20,
      background: 'rgba(0, 0, 0, 0.78)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: '1.5rem',
    }}>
      <p style={{ fontSize: '2rem', color: '#ffd54f', margin: 0 }}>⏸ Game Paused</p>
      {isHost && (
        <button
          onClick={() => send('resume')}
          style={{ background: '#1565c0', color: '#fff', fontSize: '1.1rem', padding: '0.7rem 2.5rem', borderRadius: 8 }}
        >
          ▶ Continue
        </button>
      )}
    </div>
  );
}
