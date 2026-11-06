import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import cesium from 'vite-plugin-cesium';

export default defineConfig(({ mode }) => {
	const env = loadEnv(mode, process.cwd(), 'VITE_');
	const regionView = env.VITE_REGION_VIEW ? JSON.parse(env.VITE_REGION_VIEW) : null;
	if (regionView !== null && (!Array.isArray(regionView) || regionView.length !== 5 ||
		!regionView.every(Number.isFinite) || regionView[0] < -180 || regionView[2] > 180 ||
		regionView[1] < -90 || regionView[3] > 90 || regionView[0] >= regionView[2] ||
		regionView[1] >= regionView[3] || regionView[4] <= 0)) {
		throw new Error('VITE_REGION_VIEW must contain west, south, east, north and positive camera height');
	}
	return {
	define: {
		__REGION_NAME__: JSON.stringify(env.VITE_REGION_NAME || 'Walcheren'),
		__PET_RESOLUTION__: JSON.stringify(Number(env.VITE_PET_RESOLUTION || 1)),
		__REGION_VIEW__: JSON.stringify(regionView),
	},
	plugins: [react(), cesium()],
	server: {
		proxy: {
			'/qgis-api': {
				target: 'http://qgis:8000',
				changeOrigin: true,
				rewrite: (p) => p.replace(/^\/qgis-api/, ''),
			},
			'/backend': {
				target: 'http://backend:8000',
				changeOrigin: true,
				rewrite: (p) => p.replace(/^\/backend/, ''),
			},
			'/nginx': {
				target: 'http://nginx:80',
				changeOrigin: true,
				rewrite: (p) => p.replace(/^\/nginx/, ''),
				configure: (proxy) => {
					// add permissive CORS headers on the fly (dev only)
					proxy.on('proxyRes', (proxyRes) => {
						proxyRes.headers['Access-Control-Allow-Origin'] = '*';
						proxyRes.headers['Access-Control-Allow-Headers'] = '*';
					});
				},
			},
		},
	},
	};
});
