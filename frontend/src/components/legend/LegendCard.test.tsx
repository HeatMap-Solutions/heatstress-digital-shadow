import { render, screen } from '@testing-library/react';
import { LegendCard } from './LegendCard';
import type { LegendPayload } from '../../features/wms-overlay/useWMSLegend';

const legend: LegendPayload = {
	renderer: { type: 'singlebandpseudocolor', band: 1, classification_min: -2,
		classification_max: 30, opacity: 1 },
	color_ramp: { items: [{ value: -2, color: '#2166ac' }, { value: 5, color: '#ffffff' },
		{ value: 30, color: '#b2182b' }] },
};

it('places the elevation colour stop at its numerical position', () => {
	const { container } = render(<LegendCard legend={legend} title="Elevation" />);
	const styles = Array.from(container.querySelectorAll('[style]')).map(el => el.getAttribute('style')).join(' ');
	expect(styles).toContain('21.875%');
	expect(screen.getByText('-2.00')).toBeInTheDocument();
	expect(screen.getByText('30.00')).toBeInTheDocument();
});

it('shows wind classes as labelled swatches rather than a continuous temperature scale', () => {
	const wind: LegendPayload = { renderer: { ...legend.renderer, type: 'graduatedSymbol' },
		color_ramp: { items: [{ value: 6, label: '6 - 8 m/s', color: '239,138,98,255' }] } };
	render(<LegendCard legend={wind} title="Wind" />);
	expect(screen.getByText('6 - 8 m/s')).toBeInTheDocument();
	expect(screen.queryByText('14.00')).not.toBeInTheDocument();
});
