import type { LegendPayload } from '../../features/wms-overlay/useWMSLegend';

type LegendCardProps = {
	legend: LegendPayload;
	title?: string;
};

function parseRgba(color: string): string {
	const parts = color.split(',').map((part) => Number(part.trim()));
	if (parts.length < 3 || parts.some((value) => Number.isNaN(value))) {
		return color;
	}

	const [r, g, b, a = 255] = parts;
	return `rgba(${r}, ${g}, ${b}, ${a / 255})`;
}

function formatValue(value: number): string {
	if (!Number.isFinite(value)) return String(value);
	return value.toFixed(2);
}

export const LegendCard = ({ legend, title = 'Legend' }: LegendCardProps) => {
	const items = legend.color_ramp.items ?? [];
	const sortedItems = [...items].sort((a, b) => a.value - b.value);
	const minValue = legend.renderer.classification_min;
	const maxValue = legend.renderer.classification_max;
	const gradientColors = sortedItems.map((item) =>
		`${parseRgba(item.color)} ${(item.value - minValue) / (maxValue - minValue || 1) * 100}%`).join(', ');
	const discrete = legend.renderer.type === 'graduatedSymbol';
	const midValue = (minValue + maxValue) / 2;
	const gradientWidth = '200px';

	return (
		<div
			style={{
				padding: '12px',
				background: '#ffffff',
				borderRadius: '12px',
				boxShadow: '0 4px 20px rgba(0,0,0,0.2)',
				border: '1px solid rgba(0,0,0,0.15)',
				width: '240px',
				pointerEvents: 'auto',
				color: '#000000',
				display: 'flex',
				flexDirection: 'column',
				gap: '8px',
			}}
		>
			<div style={{ fontSize: '13px', fontWeight: 700, textAlign: 'center' }}>{title}</div>
			{discrete ? sortedItems.map((item) => (
				<div key={item.value} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
					<span style={{ width: 16, height: 12, background: parseRgba(item.color) }} />
					<span>{item.label ?? formatValue(item.value)}</span>
				</div>
			)) : (
			<div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
				<div
					style={{
						width: gradientWidth,
						height: '16px',
						borderRadius: '8px',
						background: gradientColors
							? `linear-gradient(to right, ${gradientColors})`
							: '#e5e5e5',
						border: '1px solid rgba(0,0,0,0.2)',
						alignSelf: 'center',
					}}
				/>
				<div
					style={{
						display: 'flex',
						justifyContent: 'space-between',
						fontSize: '12px',
						color: '#111111',
						width: gradientWidth,
						margin: '0 auto',
					}}
				>
					<div>{formatValue(minValue)}</div>
					<div>{formatValue(midValue)}</div>
					<div>{formatValue(maxValue)}</div>
				</div>
			</div>
			)}
		</div>
	);
};
