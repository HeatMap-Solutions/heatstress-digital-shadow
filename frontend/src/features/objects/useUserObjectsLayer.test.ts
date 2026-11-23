import { act, renderHook, waitFor } from '@testing-library/react';
import { useUserObjectsLayer, type ObjectInstance } from './useUserObjectsLayer';

jest.mock('../../map/utils/crs', () => ({ lonLatToRd: () => [31740, 391420] }));

test('saves stable object IDs and sends an empty snapshot when the last object is removed', async () => {
	const originalFetch = globalThis.fetch;
	const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => [] });
	globalThis.fetch = fetchMock;
	localStorage.clear();
	try {
		const selectType = jest.fn();
		const { result } = renderHook(() => useUserObjectsLayer(true, true, 'Trees', selectType));
		await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/backend/measures'));
		const tree = {
			id: 'CLIENT-tree-123', objectType: 'Trees', position: [3.61, 51.5, 0],
			height: 15, radius: 5, geometry: 'circle', scale: 1,
		} as ObjectInstance;
		await act(async () => result.current.saveObjects([tree]));
		const first = fetchMock.mock.calls.find(([url]) => url === '/backend/update-pet');
		expect(JSON.parse(first?.[1].body)).toEqual({ points: [{
			id: tree.id, x: 31740, y: 391420, height: 15, radius: 5, geometry: 'circle',
		}] });
		expect(result.current.objectsVersion).toBe(1);
		await act(async () => result.current.saveObjects([]));
		const saves = fetchMock.mock.calls.filter(([url]) => url === '/backend/update-pet');
		expect(JSON.parse(saves[1][1].body)).toEqual({ points: [] });
		expect(result.current.objectsVersion).toBe(2);
	} finally {
		globalThis.fetch = originalFetch;
		localStorage.clear();
	}
});
