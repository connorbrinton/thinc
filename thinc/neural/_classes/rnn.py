from .model import Model
from ... import describe
from ...describe import Dimension, Synapses, Biases, Gradient
from ...api import wrap, layerize
from .._lsuv import svd_orthonormal
from ..util import copy_array


def BiLSTM(nO, nI):
    return Bidirectional(LSTM(nO//2, nI), LSTM(nO//2, nI))


def LSTM(nO, nI):
    weights = LSTM_weights(nO, nI)
    gates = LSTM_gates(weights.ops)
    return Recurrent(RNN_step(weights, gates))


def Bidirectional(l2r, r2l):
    nO = l2r.nO
    def birnn_fwd(Xs, drop=0.):
        l2r_Zs, bp_l2r_Zs = l2r.begin_update(Xs, drop=drop) 
        r2l_Zs, bp_r2l_Zs = r2l.begin_update([l2r.ops.xp.ascontiguousarray(X[::-1])
                                              for X in Xs]) 
        def birnn_bwd(dZs, sgd=None):
            d_l2r_Zs = []
            d_r2l_Zs = []
            for dZ in dZs:
                l2r_fwd = dZ[:, :nO]
                r2l_fwd = dZ[:, nO:]
                d_l2r_Zs.append(l2r.ops.xp.ascontiguousarray(l2r_fwd))
                d_r2l_Zs.append(l2r.ops.xp.ascontiguousarray(r2l_fwd[::-1]))
            dXs_l2r = bp_l2r_Zs(d_l2r_Zs, sgd=sgd)
            dXs_r2l = bp_r2l_Zs(d_r2l_Zs, sgd=sgd)
            dXs = [dXf+dXb[::-1] for dXf, dXb in zip(dXs_l2r, dXs_r2l)]
            return dXs
        Zs = [l2r.ops.xp.hstack((Zf, Zb[::-1])) for Zf, Zb in zip(l2r_Zs, r2l_Zs)]
        return Zs, birnn_bwd
    return wrap(birnn_fwd, l2r, r2l)


def Recurrent(step_model):
    ops = step_model.ops
    def recurrent_fwd(seqs, drop=0.):
        lengths = [len(X) for X in seqs]
        X = pad_batch(ops, seqs)
        Y = ops.allocate((X.shape[0], X.shape[1], step_model.nO))
        if drop != 0. and drop != None:
            cell_drop = ops.get_dropout_mask((len(seqs), step_model.nO), drop)
            hidden_drop = ops.get_dropout_mask((len(seqs), step_model.nO), drop)
            out_drop = ops.get_dropout_mask((len(seqs), step_model.nO), drop)
        else:
            hidden_drop = None
            cell_drop = None
            out_drop = None
        backprops = [None] * max(lengths)
        state = step_model.weights.get_initial_state(len(seqs))
        for t in range(max(lengths)):
            state = list(state)
            if hidden_drop is not None:
                state[0] *= cell_drop
            if cell_drop is not None:
                state[1] *= hidden_drop
            (state, Y[t]), backprops[t] = step_model.begin_update((state, X[t]),
                                                                  drop=0.0)
            if out_drop is not None:
                Y[t] *= out_drop
        outputs = unpad_batch(step_model.ops, Y, lengths)

        def recurrent_bwd(d_outputs, sgd=None):
            lengths = [len(d_o) for d_o in d_outputs]
            dY = pad_batch(step_model.ops, d_outputs)
            d_state = [step_model.ops.allocate((len(lengths), step_model.nO)),
                       step_model.ops.allocate((len(lengths), step_model.nO))]
            updates = {}
            def gather_updates(weights, gradient, key=None):
                updates[key] = (weights, gradient)

            dX = step_model.ops.allocate((dY.shape[0], dY.shape[1], step_model.weights.nI))
            for t in range(max(lengths)-1, -1, -1):
                if out_drop is not None:
                    dY[t] *= out_drop
                d_state, dX[t] = backprops[t]((d_state, dY[t]), sgd=gather_updates)
                d_state = list(d_state)
                if cell_drop is not None:
                    d_state[0] *= cell_drop
                if hidden_drop is not None:
                    d_state[1] *= hidden_drop
            d_cell, d_hidden = d_state
            step_model.weights.d_initial_cells += d_cell.sum(axis=0)
            step_model.weights.d_initial_hiddens += d_hidden.sum(axis=0)
            if sgd is not None:
                for key, (weights, gradient) in updates.items():
                    sgd(weights, gradient, key=key)
            return unpad_batch(step_model.ops, dX, lengths)
        return outputs, recurrent_bwd
    model = wrap(recurrent_fwd, step_model)
    model.nO = step_model.nO
    return model


def RNN_step(weights, gates):
    def rnn_step_fwd(prevstate_inputs, drop=0.):
        prevstate, inputs = prevstate_inputs
        cell_tm1, hidden_tm1 = prevstate

        acts, bp_acts = weights.begin_update((inputs, hidden_tm1), drop=drop)
        (cells, hiddens), bp_gates = gates.begin_update((acts, cell_tm1), drop=drop)

        def rnn_step_bwd(d_state_d_hiddens, sgd=None):
            (d_cells, d_hiddens), d_hiddens = d_state_d_hiddens
            d_acts, d_cell_tm1 = bp_gates((d_cells, d_hiddens), sgd=sgd)
            d_inputs, d_hidden_tm1 = bp_acts(d_acts, sgd=sgd)
            return (d_cell_tm1, d_hidden_tm1), d_inputs
        return ((cells, hiddens), hiddens), rnn_step_bwd
    model = wrap(rnn_step_fwd, weights, gates)
    model.nO = weights.nO
    model.nI = weights.nI
    model.weights = weights
    model.gates = gates
    return model


def LSTM_gates(ops):
    def lstm_gates_fwd(acts_prev_cells, drop=0.):
        acts, prev_cells = acts_prev_cells
        new_cells = ops.allocate(prev_cells.shape)
        new_hiddens = ops.allocate(prev_cells.shape)
        for i in range(new_hiddens.shape[0]):
            ops.lstm(new_hiddens[i], new_cells[i],
                acts[i], prev_cells[i])
        state = (new_cells, new_hiddens)

        def lstm_gates_bwd(d_state, sgd=None):
            d_cells, d_hiddens = d_state
            d_acts = ops.allocate(acts.shape)
            d_prev_cells = ops.allocate(d_cells.shape)
            for i in range(d_cells.shape[0]):
                ops.backprop_lstm(d_cells[i], d_prev_cells[i], d_acts[i],
                    d_hiddens[i], acts[i], new_cells[i], prev_cells[i])
            return d_acts, d_prev_cells
        return state, lstm_gates_bwd
    return layerize(lstm_gates_fwd)


@describe.attributes(
    nO=Dimension("Output size"),
    nI=Dimension("Input size"),
    W=Synapses("Weights matrix",
        lambda obj: (obj.nO * 4, obj.nI + obj.nO),
        lambda W, ops: copy_array(W, svd_orthonormal(W.shape))),
    b=Biases("Bias vector",
        lambda obj: (obj.nO * 4,)),
    forget_bias=Biases("Bias for forget gates",
        lambda obj: (obj.nO,)),
    d_W=Gradient("W"),
    d_b=Gradient("b"),
    d_forget_bias=Gradient("forget_bias"),
    initial_hiddens=Biases("Initial hiddens", lambda obj: (obj.nO,)),
    initial_cells=Biases("Initial cells", lambda obj: (obj.nO,)),
    d_initial_hiddens=Gradient("initial_hiddens"),
    d_initial_cells=Gradient("initial_cells")
)
class LSTM_weights(Model):
    def __init__(self, nO, nI):
        Model.__init__(self)
        self.nO = nO
        self.nI = nI

    def begin_update(self, inputs_hidden, drop=0.):
        inputs, hidden = inputs_hidden
        X = self.ops.xp.hstack([inputs, hidden])
        acts = self.ops.gemm(X, self.W, trans2=True) + self.b
        acts = acts.reshape((acts.shape[0], 4, self.nO))
        acts[:, 0] -= self.forget_bias
        acts = acts.reshape((acts.shape[0], 4 * self.nO))
        def bwd_lstm_weights(d_acts, sgd=None):
            dX = self.ops.gemm(d_acts, self.W)
            self.d_W += self.ops.gemm(d_acts, X, trans1=True)
            self.d_b += d_acts.sum(axis=0)
            self.d_forget_bias += d_acts[0].sum(axis=0)
            d_input = dX[:, :self.nI]
            d_hidden = dX[:, self.nI:]
            if sgd is not None:
                sgd(self._mem.weights, self._mem.gradient, key=self.id)
            return d_input, d_hidden
        return acts, bwd_lstm_weights

    def get_initial_state(self, n):
        initial_cells = self.ops.allocate((n, self.nO))
        initial_hiddens = self.ops.allocate((n, self.nO))
        initial_cells += self.initial_cells
        initial_hiddens += self.initial_hiddens
        return (initial_cells, initial_hiddens)


def pad_batch(ops, seqs):
    nB = len(seqs)
    nS = max([len(seq) for seq in seqs])
    arr = ops.allocate((nB, nS) + seqs[0].shape[1:], dtype=seqs[0].dtype)
    for i, seq in enumerate(seqs):
        arr[i, :len(seq)] = ops.asarray(seq)
    arr = ops.xp.ascontiguousarray(arr.transpose((1, 0) + tuple(range(2, len(arr.shape)))))
    return arr
 

def unpad_batch(ops, arr, lengths):
    seqs = []
    arr = ops.xp.ascontiguousarray(arr.transpose((1, 0) + tuple(range(2, len(arr.shape)))))
    for i in range(arr.shape[0]):
        seqs.append(arr[i, :lengths[i]])
    return seqs
