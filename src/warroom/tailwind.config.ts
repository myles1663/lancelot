import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Risk Tier Colors (spec Section 2.1)
        tier: {
          t0: '#22C55E',
          t1: '#F59E0B',
          t2: '#F97316',
          t3: '#EF4444',
        },
        // System State Colors
        state: {
          healthy: '#22C55E',
          degraded: '#F59E0B',
          error: '#EF4444',
          inactive: '#6B7280',
        },
        // Surface Colors (dark theme)
        surface: {
          primary: '#0F1117',
          card: '#1A1D27',
          'card-elevated': '#242735',
          input: '#2A2D3A',
        },
        // Border Colors
        border: {
          default: '#2E3140',
          active: '#4A5568',
        },
        // Text Colors
        text: {
          primary: '#F3F4F6',
          secondary: '#9CA3AF',
          muted: '#6B7280',
        },
        // Accent Colors
        accent: {
          primary: '#6366F1',
          secondary: '#8B5CF6',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      fontSize: {
        'metric': ['1.75rem', { lineHeight: '2rem', fontWeight: '700' }],
        'metric-label': ['0.6875rem', { lineHeight: '1rem', fontWeight: '500', letterSpacing: '0.05em' }],
      },
      keyframes: {
        'slide-in': {
          '0%': { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
      },
      animation: {
        'slide-in': 'slide-in 0.3s ease-out',
      },
    },
  },
  plugins: [],
}

export default config
