import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ReviewCanvas, type CanvasObject } from './ReviewCanvas';
import { colorFor, textColorFor } from './reviewWorkspace';
import { useCanvasStore } from '../../store/canvasStore';

describe('ReviewCanvas', () => {
  const sampleObjects: CanvasObject[] = [
    {
      object_id: 1,
      label: 'car',
      bbox: [10, 10, 50, 50],
      coordinate_system: 'zero_based_continuous',
      source_id: 1042,
      source_key: 'item_a',
    },
    {
      object_id: 2,
      label: 'pedestrian',
      bbox: [12, 12, 52, 52],
      coordinate_system: 'zero_based_continuous',
      source_id: 'src_2',
    },
  ];

  it('renders compact numbered badge markers with display numbers', () => {
    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const badge1 = screen.getByText('#1', { selector: '.canvas-badge-text' });
    const badge2 = screen.getByText('#2', { selector: '.canvas-badge-text' });
    expect(badge1).toBeInTheDocument();
    expect(badge2).toBeInTheDocument();
  });

  it('uses matching color for object geometry and marker', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const color1 = colorFor(1);
    const badgeRect = container.querySelector('g[class*="canvas-badge"] rect');
    expect(badgeRect).toHaveAttribute('fill', color1);

    const boxRect = container.querySelector('rect[stroke="' + color1 + '"]');
    expect(boxRect).toBeInTheDocument();
  });

  it('computes contrasting text colour for badge background', () => {
    expect(['#000', '#fff']).toContain(textColorFor(1));
    expect(['#000', '#fff']).toContain(textColorFor(2));
  });

  it('draws a leader line when overlapping badges are displaced', () => {
    // Both objects share the exact same tiny bounds, forcing badge displacement
    const overlapping: CanvasObject[] = [
      { object_id: 1, label: 'box1', bbox: [20, 20, 30, 30], coordinate_system: 'zero_based_continuous' },
      { object_id: 2, label: 'box2', bbox: [20, 20, 30, 30], coordinate_system: 'zero_based_continuous' },
      { object_id: 3, label: 'box3', bbox: [20, 20, 30, 30], coordinate_system: 'zero_based_continuous' },
    ];
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={overlapping}
        findings={[]}
      />,
    );

    // At least one displaced badge will have a leader line
    const lines = container.querySelectorAll('.canvas-badge line');
    expect(lines.length).toBeGreaterThan(0);
  });

  it('syncs selection when clicking geometry or badge', () => {
    const onSelect = vi.fn();
    useCanvasStore.setState({ selectedInstanceId: null });

    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
        onSelectObject={onSelect}
      />,
    );

    // Click badge #1
    const badge1 = screen.getByText('#1', { selector: '.canvas-badge-text' });
    fireEvent.click(badge1.closest('g')!);

    expect(onSelect).toHaveBeenCalledWith(1);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(1);

    // Badge and box should now reflect selection state
    expect(badge1.closest('g')).toHaveClass('is-selected');
  });

  it('maintains non-scaling stroke on geometry and badges for zoom alignment', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const rects = container.querySelectorAll('svg.canvas-overlay rect');
    rects.forEach((rect) => {
      expect(rect).toHaveAttribute('vector-effect', 'non-scaling-stroke');
    });
  });
});
