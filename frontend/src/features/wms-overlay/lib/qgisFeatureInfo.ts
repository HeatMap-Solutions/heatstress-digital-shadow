import { useCallback, useEffect, useRef, useState } from 'react';
import { buildGetFeatureInfoUrl } from './wmsUtils';

export interface PetFeatureInfo {
	lon: number;
	lat: number;
	band: number | null;
}

interface QgisFeature {
	type: string;
	id?: string | number;
	properties?: Record<string, unknown>;
}

interface QgisFeatureCollection {
	type: string;
	features: QgisFeature[];
}

function isFeatureCollection(value: unknown): value is QgisFeatureCollection {
	if (typeof value !== 'object' || value === null || !('features' in value)) {
		return false;
	}

	const fc = value as { features: unknown };

	return Array.isArray(fc.features);
}

type Config = {
	baseUrl: string;
	layerName: string;
	valueField?: string;
};

export function useQgisFeatureInfo(config: Config) {
	const [featureInfo, setFeatureInfo] = useState<PetFeatureInfo | null>(null);

	const activeRequest = useRef(0);
	const clear = useCallback(() => {
		activeRequest.current += 1;
		setFeatureInfo(null);
	}, []);

	useEffect(() => {
		clear();
		return () => { activeRequest.current += 1; };
	}, [config.baseUrl, config.layerName, config.valueField, clear]);

	async function request(lon: number, lat: number): Promise<void> {
		const requestId = ++activeRequest.current;
		const publish = (value: PetFeatureInfo | null) => {
			if (requestId === activeRequest.current) setFeatureInfo(value);
		};
		const url = buildGetFeatureInfoUrl({
			baseUrl: config.baseUrl,
			layerName: config.layerName,
			coord: [lon, lat],
			infoFormat: 'application/json',
		});

		let json: unknown;
		try {
			const res = await fetch(url);
			if (!res.ok) {
				publish(null);
				return;
			}
			json = await res.json();
		} catch {
			publish(null);
			return;
		}

		if (!isFeatureCollection(json)) {
			publish(null);
			return;
		}

		const firstFeature = json.features[0];
		const properties: Record<string, unknown> = firstFeature?.properties ?? {};

		const rawBand = config.valueField ? properties[config.valueField] :
			properties['Band 1'] ?? properties['band_1'] ?? null;

		let band: number | null = null;

		if (typeof rawBand === 'number') {
			band = rawBand;
		} else if (typeof rawBand === 'string') {
			const parsed = Number(rawBand);
			if (!Number.isNaN(parsed)) {
				band = parsed;
			}
		}

		publish({ lon, lat, band });
	}

	return { featureInfo, request, clear };
}
