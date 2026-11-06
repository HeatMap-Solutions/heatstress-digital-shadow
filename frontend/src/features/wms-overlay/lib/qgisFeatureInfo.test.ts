import { renderHook, act, waitFor } from '@testing-library/react';
import { useQgisFeatureInfo } from './qgisFeatureInfo';
import { buildGetFeatureInfoUrl } from './wmsUtils';

jest.mock('./wmsUtils', () => ({
	buildGetFeatureInfoUrl: jest.fn(),
}));

function mockResponse(body: unknown, ok = true): Partial<Response> {
	return {
		ok,
		json: jest.fn().mockResolvedValue(body),
	};
}

const originalFetch = globalThis.fetch;

describe('useQgisFeatureInfo', () => {
	const baseUrl = '/qgis';
	const layerName = 'pet-version-1';

	beforeEach(() => {
		jest.clearAllMocks();
		globalThis.fetch = jest.fn();
		(buildGetFeatureInfoUrl as jest.Mock).mockReturnValue('/qgis?dummy');
	});

	afterEach(() => {
		globalThis.fetch = originalFetch;
	});

	test('initially has null featureInfo', () => {
		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);
		expect(result.current.featureInfo).toBeNull();
	});

	test('requests PET at each clicked coordinate', async () => {
		const mockFetch = globalThis.fetch as jest.Mock;

		mockFetch.mockResolvedValue(
			mockResponse({
				type: 'FeatureCollection',
				features: [{ properties: { 'Band 1': 10 } }],
			}) as Response,
		);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.5);
		});

		await waitFor(() =>
			expect(result.current.featureInfo).toEqual({
				lon: 3.61,
				lat: 51.5,
				band: 10,
			}),
		);

		mockFetch.mockClear();

		await act(async () => {
			await result.current.request(3.7, 51.6);
		});

		expect(mockFetch).toHaveBeenCalledWith('/qgis?dummy');
		expect(result.current.featureInfo).toEqual({ lon: 3.7, lat: 51.6, band: 10 });
	});

	test('sets featureInfo when response contains numeric Band 1', async () => {
		(buildGetFeatureInfoUrl as jest.Mock).mockReturnValue('/qgis?numeric');
		const mockFetch = globalThis.fetch as jest.Mock;
		mockFetch.mockResolvedValue(
			mockResponse({
				type: 'FeatureCollection',
				features: [{ properties: { 'Band 1': 29.88 } }],
			}) as Response,
		);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.5);
		});

		await waitFor(() => expect(result.current.featureInfo).not.toBeNull());

		expect(buildGetFeatureInfoUrl).toHaveBeenCalledWith({
			baseUrl,
			layerName,
			coord: [3.61, 51.5],
			infoFormat: 'application/json',
		});

		expect(result.current.featureInfo).toEqual({
			lon: 3.61,
			lat: 51.5,
			band: 29.88,
		});
	});

	test('parses string Band 1 to number', async () => {
		const mockFetch = globalThis.fetch as jest.Mock;
		mockFetch.mockResolvedValue(
			mockResponse({
				type: 'FeatureCollection',
				features: [{ properties: { 'Band 1': '42.5' } }],
			}) as Response,
		);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.505);
		});

		await waitFor(() => expect(result.current.featureInfo).not.toBeNull());

		expect(result.current.featureInfo?.band).toBeCloseTo(42.5);
	});

	test('sets band to null when property is missing or invalid', async () => {
		const mockFetch = globalThis.fetch as jest.Mock;
		mockFetch.mockResolvedValue(
			mockResponse({
				type: 'FeatureCollection',
				features: [{ properties: {} }],
			}) as Response,
		);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.505);
		});

		await waitFor(() => expect(result.current.featureInfo).not.toBeNull());

		expect(result.current.featureInfo?.band).toBeNull();
	});

	test('clears featureInfo when HTTP status is not ok', async () => {
		const mockFetch = globalThis.fetch as jest.Mock;
		mockFetch.mockResolvedValue({ ok: false } as Response);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.5);
		});

		expect(result.current.featureInfo).toBeNull();
	});

	test('clears featureInfo when JSON is not a FeatureCollection', async () => {
		const mockFetch = globalThis.fetch as jest.Mock;
		mockFetch.mockResolvedValue(mockResponse({ foo: 'bar' }) as Response);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.5);
		});

		expect(result.current.featureInfo).toBeNull();
	});

	test('clear() resets featureInfo to null', async () => {
		const mockFetch = globalThis.fetch as jest.Mock;
		mockFetch.mockResolvedValue(
			mockResponse({
				type: 'FeatureCollection',
				features: [{ properties: { 'Band 1': 10 } }],
			}) as Response,
		);

		const { result } = renderHook(() =>
			useQgisFeatureInfo({
				baseUrl,
				layerName,
			}),
		);

		await act(async () => {
			await result.current.request(3.61, 51.5);
		});

		await waitFor(() => expect(result.current.featureInfo).not.toBeNull());

		act(() => {
			result.current.clear();
		});

		expect(result.current.featureInfo).toBeNull();
	});
});

it.each([[7.5, 7.5], [null, null]])('reads the selected wind field and preserves missing wind (%s)', async (wind, expected) => {
	const previousFetch = globalThis.fetch;
	globalThis.fetch = jest.fn().mockResolvedValue(mockResponse({
		type: 'FeatureCollection', features: [{ properties: { geschaalde_u_1_2: wind, u_1_2: .5 } }],
	}));
	try {
		const { result } = renderHook(() => useQgisFeatureInfo({
			baseUrl: '/backend/qgis/wms', layerName: 'wind', valueField: 'geschaalde_u_1_2',
		}));
		await act(async () => { await result.current.request(3.613, 51.498); });
		expect(result.current.featureInfo?.band).toBe(expected);
	} finally {
		globalThis.fetch = previousFetch;
	}
});


describe('feature info layer changes', () => {
 beforeEach(() => {
  globalThis.fetch = jest.fn();
  (buildGetFeatureInfoUrl as jest.Mock).mockReturnValue('/qgis?dummy');
 });
 afterEach(() => { globalThis.fetch = originalFetch; });

 test('clears the old measurement when the selected layer changes', async () => {
  (globalThis.fetch as jest.Mock).mockResolvedValue(mockResponse({
   type: 'FeatureCollection', features: [{ properties: { 'Band 1': 7.69 } }],
  }));
  const { result, rerender } = renderHook(({ layerName }) =>
   useQgisFeatureInfo({ baseUrl: '/qgis', layerName }),
   { initialProps: { layerName: 'dsm' } },
  );
  await act(async () => { await result.current.request(4.053, 51.280); });
  expect(result.current.featureInfo?.band).toBe(7.69);
  rerender({ layerName: 'wind' });
  expect(result.current.featureInfo).toBeNull();
 });

 test('ignores a response from a layer that is no longer selected', async () => {
  let finish!: (response: Partial<Response>) => void;
  (globalThis.fetch as jest.Mock).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const { result, rerender } = renderHook(({ layerName }) =>
   useQgisFeatureInfo({ baseUrl: '/qgis', layerName }),
   { initialProps: { layerName: 'dsm' } },
  );
  let request!: Promise<void>;
  act(() => { request = result.current.request(4.053, 51.280); });
  rerender({ layerName: 'wind' });
  await act(async () => {
   finish(mockResponse({ type: 'FeatureCollection', features: [{ properties: { 'Band 1': 7.69 } }] }));
   await request;
  });
  expect(result.current.featureInfo).toBeNull();
 });
});
