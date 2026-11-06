import { act, renderHook, waitFor } from '@testing-library/react';
import { useUserObjectsLayer } from './useUserObjectsLayer';
import { LOCAL_STORAGE_KEY } from '../../map/utils/constants';

jest.mock('../../map/utils/crs', () => ({ lonLatToRd: () => [31000, 391000] }));

const originalFetch = globalThis.fetch;
const measure = { id: 1, name: 'Trees', model: '', icon: '', scale: 1, height: 15,
	radius: 5, geometry: 'circle', rotation: [0, 0, 0] };
const response = (ok: boolean, body: unknown) => ({ ok, status: ok ? 200 : 400,
	statusText: ok ? 'OK' : 'Bad Request', json: async () => body } as Response);

it('retains a rejected draft and displays its reason, then clears the error on successful retry', async () => {
	localStorage.clear();
	const errors = jest.spyOn(console, 'error').mockImplementation(() => {});
	const replies = [response(false, { detail: { detail: 'Object is outside supported Walcheren land' } }),
		response(true, { status: 'success' })];
	globalThis.fetch = jest.fn(async (url) => String(url).endsWith('/measures')
		? response(true, [measure]) : replies.shift()!);
	const select = jest.fn();
	try {
		const { result } = renderHook(() => useUserObjectsLayer(true, true, 'Trees', select));
		await waitFor(() => expect(result.current.objectTypes).toHaveLength(1));
		act(() => { result.current.handleInteraction(3.58, 51.65); });
		const draft = result.current.objectsToSave;
		await act(async () => { await result.current.saveObjects(draft); });
		expect(result.current.error?.message).toBe('Object is outside supported Walcheren land');
		expect(result.current.objectsToSave).toEqual(draft);
		expect(result.current.hasUnsavedChanges).toBe(true);
		expect(localStorage.getItem(LOCAL_STORAGE_KEY)).toBeNull();
		await act(async () => { await result.current.saveObjects(draft); });
		expect(result.current.error).toBeNull();
		expect(result.current.hasUnsavedChanges).toBe(false);
		expect(JSON.parse(localStorage.getItem(LOCAL_STORAGE_KEY)!)).toEqual(draft);
	} finally {
		globalThis.fetch = originalFetch;
		errors.mockRestore();
		localStorage.clear();
	}
});
