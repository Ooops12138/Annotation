/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: { ink: '#1e293b', paper: '#f8fafc', signal: '#e76f51', moss: '#2a9d8f' },
      fontFamily: { display: ['Georgia', 'serif'], sans: ['Inter', 'system-ui', 'sans-serif'] }
    }
  },
  plugins: []
}
