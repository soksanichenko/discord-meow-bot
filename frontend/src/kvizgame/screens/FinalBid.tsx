import { useState } from 'react';
import type { GameState } from '../types';
import { Scores } from './Scores';

interface FinalBidProps {
  state: GameState;
  playerId: string;
  isHost: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function FinalBid({ state, playerId, isHost, send }: FinalBidProps) {
  const { scores, player_names, final_round_name, final_theme_name, final_bids_submitted = [] } = state;
  const [bid, setBid] = useState(1);
  const hasBid = final_bids_submitted.includes(playerId);
  const total = Object.keys(scores).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1.5rem', gap: '1.5rem', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ textAlign: 'center' }}>
        <h2 style={{ color: '#ffd54f', margin: 0 }}>{final_round_name ?? 'Final Round'}</h2>
        <p style={{ color: '#90caf9', marginTop: '0.5rem' }}>Theme: <strong>{final_theme_name}</strong></p>
      </div>

      <p style={{ color: '#aaa', textAlign: 'center' }}>
        {final_bids_submitted.length}/{total} players have placed their bid
      </p>

      {!isHost && (
        hasBid ? (
          <p style={{ color: '#81c784', fontSize: '1.1rem' }}>✓ Bid placed</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem' }}>
            <p style={{ color: '#fff', margin: 0 }}>Place your bid:</p>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <input
                type="number"
                min={1}
                value={bid}
                onChange={e => setBid(Math.max(1, +e.target.value))}
                style={{ width: 100, padding: '0.4rem', fontSize: '1rem', background: '#1a2035', color: '#fff', border: '1px solid #1976d2', borderRadius: 4, textAlign: 'center' }}
              />
              <button
                onClick={() => send('place_final_bid', { amount: bid })}
                style={{ background: '#1565c0', color: '#fff', fontSize: '1rem', padding: '0.5rem 1.5rem', borderRadius: 6 }}
              >
                Bid
              </button>
            </div>
          </div>
        )
      )}

      {isHost && (
        <p style={{ color: '#90caf9', textAlign: 'center', fontSize: '0.9rem' }}>
          Waiting for all players to bid…
        </p>
      )}

      <Scores scores={scores} playerNames={player_names} />
    </div>
  );
}
