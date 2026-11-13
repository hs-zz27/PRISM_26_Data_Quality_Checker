import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ReviewCanvas, type CanvasObject } from './ReviewCanvas';
import { colorFor, textColorFor } from './reviewWorkspace';
import { useCanvasStore } from '../../store/canvasStore';
import { boundarySegments, segmentCrossesRect } from './overlayLayout';

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

  beforeEach(() => {
    useCanvasStore.setState({
      showBoxes: true,
      showPolygons: true,
      showLabels: true,
      showMask: true,
      showLegend: false,
      zoom: 1,
      pan: { x: 0, y: 0 },
      selectedInstanceId: null,
      originalOnly: false,
    });
  });

  const outline = (left: number): number[][] => [[left, 20], [left + 30, 20], [left + 30, 120], [left, 120]];
  const people: CanvasObject[] = [0, 1, 2].map((index) => ({
    object_id: index + 1, label: 'person', coordinate_system: 'zero_based_continuous' as const,
    source_key: `/shapes/${index}`, source_id: null, polygons: [outline(20 + index * 60)],
    bbox: [20 + index * 60, 20, 50 + index * 60, 120] as [number, number, number, number], bbox_origin: 'derived_from_polygon',
  }));

  it('draws polygon data as polygons only: no box, no box toggle and no box wording', () => {
    useCanvasStore.setState({ showLegend: true });
    const { container } = render(
      <ReviewCanvas imageSize={[200, 200]} objects={people} findings={[]} annotationFormat="labelme" />,
    );

    expect(container.querySelectorAll('svg.canvas-overlay g[role="button"] polygon.overlay-outline')).toHaveLength(3);
    expect(container.querySelectorAll('svg.canvas-overlay g[role="button"] rect')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Toggle boxes' })).toBeNull();
    expect(screen.getByRole('button', { name: /^Object 1, person, \/shapes\/0, polygon with 4 points$/ })).toBeInTheDocument();
    expect(screen.getByRole('note', { name: 'Overlay legend' }).textContent).not.toMatch(/box/i);
    expect(container.textContent).not.toMatch(/box/i);
  });

  it('draws the box of an object whose only geometry is a box, with the box toggle', () => {
    const mixed: CanvasObject[] = [
      people[0]!,
      { object_id: 2, label: 'person', bbox: [100, 20, 140, 120], coordinate_system: 'zero_based_continuous', bbox_origin: 'supplied', source_key: '/shapes/1' },
    ];
    const { container } = render(<ReviewCanvas imageSize={[200, 200]} objects={mixed} findings={[]} annotationFormat="labelme" />);

    const rects = container.querySelectorAll('svg.canvas-overlay g[role="button"] rect');
    expect(rects).toHaveLength(1);
    expect(rects[0]).toHaveAttribute('x', '100');
    expect(screen.getByRole('button', { name: 'Toggle boxes' })).toBeInTheDocument();
  });

  it('keeps showing the supplied box that a saved box-check finding is about', () => {
    const supplied: CanvasObject[] = [{ ...people[0]!, bbox_origin: 'supplied' }, people[1]!];
    const boxFinding = { finding_id: 'f1', object_id: 1, check_name: 'box_validity', outcome: 'fail' } as never;
    const { container } = render(
      <ReviewCanvas imageSize={[200, 200]} objects={supplied} findings={[boxFinding]} annotationFormat="coco" />,
    );
    const rects = container.querySelectorAll('svg.canvas-overlay g[role="button"] rect');
    expect(rects).toHaveLength(1);
    expect(rects[0]).toHaveAttribute('x', '20');
  });

  it('makes the selected polygon clearly different from the others, by more than colour', () => {
    useCanvasStore.setState({ selectedInstanceId: 2 });
    const { container } = render(<ReviewCanvas imageSize={[200, 200]} objects={people} findings={[]} annotationFormat="labelme" />);

    const byLabel = (number: number) => screen.getByRole('button', { name: new RegExp(`^Object ${number}, person`) });
    const [first, second, third] = [byLabel(1), byLabel(2), byLabel(3)];
    expect(second).toHaveClass('is-selected');
    expect(second).toHaveAttribute('aria-pressed', 'true');
    expect(first).toHaveAttribute('aria-pressed', 'false');
    // Selected: a dark halo under a thick outline and a tinted fill. Others: thin, unfilled and faded.
    expect(second.querySelector('polygon.overlay-halo')).toBeInTheDocument();
    const selectedOutline = second.querySelector('polygon.overlay-outline')!;
    const otherOutline = first.querySelector('polygon.overlay-outline')!;
    expect(Number(selectedOutline.getAttribute('stroke-width'))).toBeGreaterThanOrEqual(2 * Number(otherOutline.getAttribute('stroke-width')));
    expect(Number(selectedOutline.getAttribute('fill-opacity'))).toBeGreaterThan(0);
    expect(otherOutline.getAttribute('fill')).toBe('none');
    expect(first.querySelector('polygon.overlay-halo')).toBeNull();
    expect(first).toHaveClass('is-dimmed');
    expect(third).toHaveClass('is-dimmed');
    expect(second).not.toHaveClass('is-dimmed');
    // It is drawn last, so it is never hidden under another outline.
    const groups = [...container.querySelectorAll('svg.canvas-overlay g[role="button"]')];
    expect(groups[groups.length - 1]).toBe(second);
    expect(screen.getByRole('status', { name: 'Selected annotation' })).toHaveTextContent('Selected: #2 person /shapes/1');
  });

  it('shows the stable key of every polygon on the image', () => {
    render(<ReviewCanvas imageSize={[200, 200]} objects={people} findings={[]} annotationFormat="labelme" />);
    for (const index of [0, 1, 2]) {
      expect(screen.getByText(`/shapes/${index}`, { selector: '.canvas-badge-key' })).toBeInTheDocument();
      expect(screen.getByText(`#${index + 1}`, { selector: '.canvas-badge-text' })).toBeInTheDocument();
    }
  });

  it('keeps labels off every polygon boundary', () => {
    const { container } = render(<ReviewCanvas imageSize={[200, 200]} objects={people} findings={[]} annotationFormat="labelme" />);
    const segments = boundarySegments(people.map((obj) => ({ polygons: obj.polygons!, box: null })));
    const badges = [...container.querySelectorAll('g.canvas-badge > rect')].map((rect) => ({
      x: Number(rect.getAttribute('x')), y: Number(rect.getAttribute('y')),
      width: Number(rect.getAttribute('width')), height: Number(rect.getAttribute('height')),
    }));
    expect(badges).toHaveLength(3);
    for (const badge of badges) {
      expect(segments.filter((segment) => segmentCrossesRect(segment, badge))).toEqual([]);
    }
  });

  it('shows the original photo with no overlay at any time, and brings the overlay back unchanged', () => {
    useCanvasStore.setState({ selectedInstanceId: 1 });
    const assets = { image: { url: '/original.jpg' }, mask_preview: { url: '/mask.png' } };
    const { container } = render(
      <ReviewCanvas imageSize={[200, 200]} objects={people} findings={[]} annotationFormat="labelme" assets={assets} sampleId="photo" />,
    );
    const photo = screen.getByRole('img', { name: 'photo' });
    expect(container.querySelector('svg.canvas-overlay')).toBeInTheDocument();
    expect(container.querySelector('img.canvas-mask')).toBeInTheDocument();

    const toggle = screen.getByRole('button', { name: 'Show the original photo only' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(container.querySelector('svg.canvas-overlay')).toBeNull();
    expect(container.querySelector('img.canvas-mask')).toBeNull();
    expect(container.querySelectorAll('.canvas-stage img')).toHaveLength(1);
    expect(screen.getByRole('img', { name: 'photo' })).toBe(photo);
    expect(photo).toHaveAttribute('src', '/original.jpg');
    expect(screen.getByText('Original photo, nothing drawn on it')).toBeInTheDocument();
    // The choice survives the other layer switches, and the selection is kept for when the overlay returns.
    fireEvent.click(screen.getByRole('button', { name: 'Toggle polygons' }));
    fireEvent.click(screen.getByRole('button', { name: 'Toggle polygons' }));
    expect(container.querySelector('svg.canvas-overlay')).toBeNull();
    expect(useCanvasStore.getState().selectedInstanceId).toBe(1);

    fireEvent.keyDown(window, { key: 'o' });
    expect(container.querySelectorAll('svg.canvas-overlay g[role="button"] polygon.overlay-outline')).toHaveLength(3);
    expect(container.querySelector('svg.canvas-overlay g.is-selected')).toBeInTheDocument();
  });

  it('never draws into the photo: the overlay is a separate layer over the unchanged image', () => {
    const assets = { image: { url: '/original.jpg' } };
    const { container } = render(
      <ReviewCanvas imageSize={[200, 200]} objects={people} findings={[]} annotationFormat="labelme" assets={assets} sampleId="photo" />,
    );
    expect(container.querySelector('.canvas-stage canvas')).toBeNull();
    expect(container.querySelectorAll('.canvas-stage > img')).toHaveLength(1);
    expect(container.querySelector('.canvas-stage > img')).toHaveAttribute('src', '/original.jpg');
    expect(container.querySelector('.canvas-stage > svg.canvas-overlay')).toBeInTheDocument();
  });

  it('explains all symbols and layers in plain words in the legend', () => {
    useCanvasStore.setState({ showLegend: true });
    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
        isCorrection={true}
      />,
    );

    const legend = screen.getByRole('note', { name: 'Overlay legend' });
    expect(legend).toHaveTextContent('Thin line: an annotation, drawn exactly as the file has it');
    expect(legend).toHaveTextContent('Thick line with a tinted fill: the selected annotation');
    expect(legend).toHaveTextContent('Rectangle: a box from the annotation file');
    expect(legend).toHaveTextContent('#N: the number in the object list, followed by the annotation’s key in the file');
    expect(legend).toHaveTextContent('!: a finding for this annotation needs a look');
    expect(legend).toHaveTextContent('Original: hides everything drawn over the photo');
    expect(legend).not.toHaveTextContent(/derived/i);
    expect(screen.getByText(/Showing proposed correction \(preview, not saved\)/i)).toBeInTheDocument();
  });

  it('toggles visibility of boxes, polygons, labels, and mask via toolbar buttons', () => {
    useCanvasStore.setState({ showBoxes: true, showPolygons: true, showLabels: true, showMask: true });
    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const toggleBoxesBtn = screen.getByRole('button', { name: 'Toggle boxes' });
    const togglePolygonsBtn = screen.getByRole('button', { name: 'Toggle polygons' });
    const toggleLabelsBtn = screen.getByRole('button', { name: 'Toggle labels' });
    const toggleMaskBtn = screen.getByRole('button', { name: 'Toggle mask' });

    expect(toggleBoxesBtn).toHaveClass('is-on');
    fireEvent.click(toggleBoxesBtn);
    expect(useCanvasStore.getState().showBoxes).toBe(false);

    fireEvent.click(togglePolygonsBtn);
    expect(useCanvasStore.getState().showPolygons).toBe(false);

    fireEvent.click(toggleLabelsBtn);
    expect(useCanvasStore.getState().showLabels).toBe(false);

    fireEvent.click(toggleMaskBtn);
    expect(useCanvasStore.getState().showMask).toBe(false);
  });

  it('supports overlap hit-testing where smallest object wins first and clicks cycle overlapping objects', () => {
    const onSelect = vi.fn();
    useCanvasStore.setState({ selectedInstanceId: null, zoom: 1, pan: { x: 0, y: 0 } });

    // Object 1: large [10, 10, 100, 100], area = 8100
    // Object 2: small [20, 20, 40, 40], area = 400
    const overlappingObjects: CanvasObject[] = [
      { object_id: 1, label: 'large', bbox: [10, 10, 100, 100], coordinate_system: 'zero_based_continuous' },
      { object_id: 2, label: 'small', bbox: [20, 20, 40, 40], coordinate_system: 'zero_based_continuous' },
    ];

    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={overlappingObjects}
        findings={[]}
        onSelectObject={onSelect}
      />,
    );

    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 200, bottom: 200, width: 200, height: 200, x: 0, y: 0, toJSON: () => {},
    });

    const overlaySvg = container.querySelector('svg.canvas-overlay') as SVGElement;
    expect(overlaySvg).toBeInTheDocument();

    // First click at (30, 30) - inside both. Smallest object (#2) wins.
    fireEvent.click(overlaySvg, { clientX: 30, clientY: 30 });
    expect(onSelect).toHaveBeenLastCalledWith(2);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(2);

    // Second click at (30, 30) - cycles to next overlapping object (#1).
    fireEvent.click(overlaySvg, { clientX: 30, clientY: 30 });
    expect(onSelect).toHaveBeenLastCalledWith(1);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(1);

    // Third click at (30, 30) - cycles back to #2.
    fireEvent.click(overlaySvg, { clientX: 30, clientY: 30 });
    expect(onSelect).toHaveBeenLastCalledWith(2);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(2);
  });

  it('zooms to fit the selected object when zoom button is clicked', () => {
    useCanvasStore.setState({ selectedInstanceId: 1, zoom: 1, pan: { x: 0, y: 0 } });

    const singleObject: CanvasObject[] = [
      { object_id: 1, label: 'target', bbox: [20, 20, 60, 60], coordinate_system: 'zero_based_continuous' },
    ];

    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={singleObject}
        findings={[]}
      />,
    );

    const canvasRoot = container.querySelector('.review-canvas') as HTMLElement;
    vi.spyOn(canvasRoot, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 400, bottom: 400, width: 400, height: 400, x: 0, y: 0, toJSON: () => {},
    });

    const zoomToBtn = screen.getByRole('button', { name: 'Zoom to selected object' });
    fireEvent.click(zoomToBtn);

    const { zoom, pan } = useCanvasStore.getState();
    expect(zoom).toBeGreaterThan(1);
    // The stage scales around its centre, so the object's centre (40,40)
    // must move by its offset from the image centre (100,100) times zoom.
    expect(pan.x).toBeCloseTo(60 * zoom);
    expect(pan.y).toBeCloseTo(60 * zoom);
  });

  it('selects an object from the keyboard without needing a pointer position', () => {
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

    const object2 = screen.getByRole('button', { name: /^Object 2, pedestrian/ });
    fireEvent.keyDown(object2, { key: 'Enter' });

    expect(onSelect).toHaveBeenCalledWith(2);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(2);
  });

  it('never draws a guessed location for an object without a box or polygon', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={[...sampleObjects, { object_id: 3, label: 'ghost', bbox: null, coordinate_system: 'zero_based_continuous', polygons: [] }]}
        findings={[]}
      />,
    );

    expect(screen.queryByRole('button', { name: /^Object 3, ghost/ })).toBeNull();
    expect(container.querySelector('.canvas-overlay circle')).toBeNull();
    expect(screen.queryByText('#3', { selector: '.canvas-badge-text' })).toBeNull();
    expect(screen.getByRole('status', { name: 'Objects not drawn on the image' })).toHaveTextContent('#3ghost: not drawn, the annotation has no box or polygon');
  });

  it('says a polygon is missing, without box wording, for polygon data', () => {
    render(
      <ReviewCanvas imageSize={[200, 200]} annotationFormat="labelme" findings={[]}
        objects={[...people, { object_id: 4, label: 'ghost', bbox: null, coordinate_system: 'zero_based_continuous', polygons: [] }]} />,
    );
    expect(screen.getByRole('status', { name: 'Objects not drawn on the image' })).toHaveTextContent('#4ghost: not drawn, the annotation has no polygon');
  });

  it('colours box outlines by object, not by finding outcome', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const boxes = container.querySelectorAll<SVGRectElement>('g[role="button"] rect');
    expect(boxes[0]?.style.stroke).toBe(colorFor(1));
    expect(boxes[1]?.style.stroke).toBe(colorFor(2));
  });
});
