import { useEffect, useRef } from 'react';
import type { GameState } from '../types';

interface TimerBorderProps {
  phase: GameState['phase'];
  paused?: boolean;
  durationMs?: number;
}

const PERIMETER = 400; // viewBox 100×100 → 4 sides × 100

const PHASE_COLOR: Partial<Record<GameState['phase'], string>> = {
  BUZZER_OPEN: '#ef5350',
  ANSWERING: '#ffd54f',
  ANSWER_RESULT: '#ffd54f',
};

export function TimerBorder({ phase, paused = false, durationMs = 30_000 }: TimerBorderProps) {
  const rectRef = useRef<SVGRectElement>(null);
  // elapsed: ms already consumed before the current BUZZER_OPEN start
  const stateRef = useRef({ elapsed: 0, rafId: 0, startedAt: 0 });

  useEffect(() => {
    const s = stateRef.current;
    const rect = rectRef.current;
    if (!rect) return;

    cancelAnimationFrame(s.rafId);

    if (phase === 'BUZZER_OPEN' && !paused) {
      s.startedAt = Date.now() - s.elapsed;

      const tick = () => {
        s.elapsed = Date.now() - s.startedAt;
        const progress = Math.min(s.elapsed / durationMs, 1);
        rect.style.strokeDashoffset = String(PERIMETER * progress);
        if (progress < 1) s.rafId = requestAnimationFrame(tick);
      };
      s.rafId = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(s.rafId);
    }

    // Reset on new question
    if (phase === 'BOARD' || phase === 'QUESTION') {
      s.elapsed = 0;
      rect.style.strokeDashoffset = '0';
    }
    // ANSWERING / ANSWER_RESULT / paused: freeze dashoffset where it stopped
  }, [phase, paused, durationMs]);

  const visible = phase === 'BUZZER_OPEN' || phase === 'ANSWERING' || phase === 'ANSWER_RESULT';
  const color = PHASE_COLOR[phase] ?? '#1565c0';

  return (
    <svg
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
      }}
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
    >
      <rect
        ref={rectRef}
        x="0.5"
        y="0.5"
        width="99"
        height="99"
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeDasharray={String(PERIMETER)}
        strokeDashoffset="0"
        style={{ opacity: visible ? 1 : 0, transition: 'opacity 0.2s, stroke 0.2s' }}
      />
    </svg>
  );
}
