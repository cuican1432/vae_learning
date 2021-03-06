from math import log2, log10, ceil
import warnings
import torch
import numpy as np
import matplotlib

import matplotlib.pyplot as plt
from matplotlib.colors import Normalize, LogNorm, SymLogNorm
from matplotlib.cm import ScalarMappable

matplotlib.use('Agg')


def quantize(x):
    return 2 ** round(log2(x), ndigits=1)


def plt_slices(*fields, size=64, title=None, cmap=None, norm=None):
    """Plot slices of fields of more than 2 spatial dimensions.
    Each field should have a channel dimension followed by spatial dimensions,
    i.e. no batch dimension.
    """
    plt.close('all')

    assert all(isinstance(field, torch.Tensor) for field in fields)

    fields = [field.detach().cpu().numpy() for field in fields]

    nc = max(field.shape[0] for field in fields)
    nf = len(fields)

    if title is not None:
        assert len(title) == nf
    cmap = np.broadcast_to(cmap, (nf,))
    norm = np.broadcast_to(norm, (nf,))

    im_size = 2
    cbar_height = 0.2
    fig, axes = plt.subplots(
        nc + 1, nf,
        squeeze=False,
        figsize=(nf * im_size, nc * im_size + cbar_height),
        dpi=100,
        gridspec_kw={'height_ratios': nc * [im_size] + [cbar_height]}
    )

    for f, (field, cmap_col, norm_col) in enumerate(zip(fields, cmap, norm)):
        all_non_neg = (field >= 0).all()
        all_non_pos = (field <= 0).all()

        if cmap_col is None:
            if all_non_neg:
                cmap_col = 'viridis'
            elif all_non_pos:
                warnings.warn('no implementation for all non-positive values')
                cmap_col = None
            else:
                cmap_col = 'RdBu_r'

        if norm_col is None:
            l2, l1, h1, h2 = np.percentile(field, [2.5, 16, 84, 97.5])
            w1, w2 = (h1 - l1) / 2, (h2 - l2) / 2

            if all_non_neg:
                if h1 > 0.1 * h2:
                    norm_col = Normalize(vmin=0, vmax=quantize(h2))
                else:
                    norm_col = LogNorm(vmin=quantize(l2), vmax=quantize(h2))
            elif all_non_pos:
                warnings.warn('no implementation for all non-positive values yet')
                norm_col = None
            else:
                vlim = quantize(max(-l2, h2))
                if w1 > 0.1 * w2 or l1 * h1 >= 0:
                    norm_col = Normalize(vmin=-vlim, vmax=vlim)
                else:
                    linthresh = 0.1 * quantize(min(-l1, h1))
                    norm_col = SymLogNorm(linthresh=linthresh, vmin=-vlim, vmax=vlim)

        for c in range(field.shape[0]):
            s = (c,) + tuple(d // 2 for d in field.shape[1:-2])
            if size is None:
                s += (slice(None),) * 2
            else:
                s += (
                    slice(
                        (field.shape[-2] - size) // 2,
                        (field.shape[-2] + size) // 2,
                    ),
                    slice(
                        (field.shape[-1] - size) // 2,
                        (field.shape[-1] + size) // 2,
                    ),
                )

            axes[c, f].pcolormesh(field[s], cmap=cmap_col, norm=norm_col)

            axes[c, f].set_aspect('equal')

            axes[c, f].set_xticks([])
            axes[c, f].set_yticks([])

            if c == 0 and title is not None:
                axes[c, f].set_title(title[f])

        for c in range(field.shape[0], nc):
            axes[c, f].axis('off')

        fig.colorbar(
            ScalarMappable(norm=norm_col, cmap=cmap_col),
            cax=axes[-1, f],
            orientation='horizontal',
        )

    fig.tight_layout()

    return fig


def power(x):
    """Compute power spectra of input fields
    Each field should have batch and channel dimensions followed by spatial
    dimensions. Powers are summed over channels, and averaged over batches.
    Power is not normalized. Wavevectors are in unit of the fundamental
    frequency of the input.
    """
    signal_ndim = x.dim() - 2
    kmax = min(d for d in x.shape[-signal_ndim:]) // 2
    even = x.shape[-1] % 2 == 0

    x = torch.rfft(x, signal_ndim)
    P = x.pow(2).sum(dim=-1)
    P = P.mean(dim=0)
    P = P.sum(dim=0)
    del x

    k = [torch.arange(d, dtype=torch.float32, device=P.device)
         for d in P.shape]
    k = [j - len(j) * (j > len(j) // 2) for j in k[:-1]] + [k[-1]]
    k = torch.meshgrid(*k)
    k = torch.stack(k, dim=0)
    k = k.norm(p=2, dim=0)

    N = torch.full_like(P, 2, dtype=torch.int32)
    N[..., 0] = 1
    if even:
        N[..., -1] = 1

    k = k.flatten()
    P = P.flatten()
    N = N.flatten()

    kbin = k.ceil().to(torch.int32)
    k = torch.bincount(kbin, weights=k * N)
    P = torch.bincount(kbin, weights=P * N)
    N = torch.bincount(kbin, weights=N).round().to(torch.int32)
    del kbin

    # drop k=0 mode and cut at kmax (smallest Nyquist)
    k = k[1:1 + kmax]
    P = P[1:1 + kmax]
    N = N[1:1 + kmax]

    k /= N
    P /= N

    return k, P, N


def plt_power(*fields, l2e=False, label=None):
    """Plot power spectra of fields.
    Each field should have batch and channel dimensions followed by spatial
    dimensions.
    Optionally the field can be transformed by lag2eul first.
    See `map2map.models.power`.
    """
    plt.close('all')

    if label is not None:
        assert len(label) == len(fields)
    else:
        label = [None] * len(fields)

    with torch.no_grad():
        if l2e:
            fields = lag2eul(*fields)

        ks, Ps = [], []
        for field in fields:
            k, P, _ = power(field)
            ks.append(k)
            Ps.append(P)

    ks = [k.cpu().numpy() for k in ks]
    Ps = [P.cpu().numpy() for P in Ps]

    fig, axes = plt.subplots(figsize=(4.8, 3.6), dpi=150)

    for k, P, l in zip(ks, Ps, label):
        axes.loglog(k, P, label=l, alpha=0.7)

    axes.set_xlabel('unnormalized wavenumber')
    axes.set_ylabel('unnormalized power')
    axes.legend()
    fig.tight_layout()

    return fig
