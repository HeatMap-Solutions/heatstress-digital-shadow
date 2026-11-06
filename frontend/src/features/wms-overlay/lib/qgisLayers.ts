declare const __REGION_NAME__: string;
declare const __PET_RESOLUTION__: number;
declare const __REGION_VIEW__: [number, number, number, number, number] | null;
export const REGION_VIEW = typeof __REGION_VIEW__ === 'undefined' ? null : __REGION_VIEW__;
export const REGION_NAME = typeof __REGION_NAME__ === 'undefined' ? 'Walcheren' : __REGION_NAME__;
export const PET_RESOLUTION = typeof __PET_RESOLUTION__ === 'undefined' ? 1 : __PET_RESOLUTION__;

type QgisOverlayLayer = {
	id: string;
	label: string;
	valueLabel?: string;
	unit?: string;
	valueField?: string;
};

export const QGIS_OVERLAY_LAYERS: QgisOverlayLayer[] = [
	{ id: 'pet-version-1', label: `${REGION_NAME} PET (${PET_RESOLUTION > 1 ? `${PET_RESOLUTION} m, ` : ''}experimental)` },
	{
		id: 'pet-session-updated',
		label: 'PET (Updated)',
		valueLabel: 'PET Index',
		unit: '°C',
	},
	{ id: 'wind', label: 'Wind (PET input)', valueLabel: 'Pedestrian wind', unit: 'm/s', valueField: 'geschaalde_u_1_2' },
	{ id: 'wind-speed-calc', label: 'Wind speed (calc)', valueLabel: 'Morphology wind', unit: 'm/s', valueField: 'u_1_2' },
	{ id: 'R_65DN1', label: 'Elevation (DSM)', valueLabel: 'Surface elevation', unit: 'm NAP' },
	{ id: 'SVF_r65dn1', label: 'SVF (Sky View Factor)', valueLabel: 'Sky View Factor' },
	{ id: 'bowen-ratio-middelburg', label: 'Bowen ratio', valueLabel: 'Bowen ratio' },
	{ id: 'vegetation-lgn', label: 'Vegetation (LGN proxy)', valueLabel: 'Vegetation proxy' },
] as const;

export type QgisLayerId = (typeof QGIS_OVERLAY_LAYERS)[number]['id'];
