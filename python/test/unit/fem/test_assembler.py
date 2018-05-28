# Copyright (C) 2018 Garth N. Wells
#
# This file is part of DOLFIN (https://www.fenicsproject.org)
#
# SPDX-License-Identifier:    LGPL-3.0-or-later
"""Unit tests for assembly"""

import math
import os

import numpy
import pytest

import dolfin
import ufl
from dolfin_utils.test import skip_in_parallel
from ufl import dx
from petsc4py import PETSc
from slepc4py import SLEPc


def test_matrix_assembly_block():
    """Test assembly of block matrices and vectors into (a) monolithic
    blocked structures, PETSc Nest structures, and monolithic structures.
    """

    mesh = dolfin.generation.UnitSquareMesh(dolfin.MPI.comm_world, 4, 8)

    p0, p1 = 1, 2
    P0 = ufl.FiniteElement("Lagrange", mesh.ufl_cell(), p0)
    P1 = ufl.FiniteElement("Lagrange", mesh.ufl_cell(), p1)

    V0 = dolfin.function.functionspace.FunctionSpace(mesh, P0)
    V1 = dolfin.function.functionspace.FunctionSpace(mesh, P1)

    def boundary(x):
        return numpy.logical_or(x[:, 0] < 1.0e-6, x[:, 0] > 1.0 - 1.0e-6)

    u_bc = dolfin.function.constant.Constant(50.0)
    bc = dolfin.fem.dirichletbc.DirichletBC(V1, u_bc, boundary)

    # Define variational problem
    u, p = dolfin.function.argument.TrialFunction(
        V0), dolfin.function.argument.TrialFunction(V1)
    v, q = dolfin.function.argument.TestFunction(
        V0), dolfin.function.argument.TestFunction(V1)
    f = dolfin.function.constant.Constant(1.0)
    g = dolfin.function.constant.Constant(-3.0)
    zero = dolfin.function.constant.Constant(0.0)

    a00 = u * v * dx
    a01 = v * p * dx
    a10 = q * u * dx
    a11 = q * p * dx
    # a11 = None

    L0 = zero * f * v * dx
    L1 = g * q * dx

    # Create assembler
    assembler = dolfin.fem.assembling.Assembler([[a00, a01], [a10, a11]],
                                                [L0, L1], [bc])

    # Monolithic blocked
    A0, b0 = assembler.assemble(
        mat_type=dolfin.cpp.fem.Assembler.BlockType.monolithic)
    assert A0.mat().getType() != "nest"
    Anorm0 = A0.mat().norm()
    bnorm0 = b0.vec().norm()

    # Nested (MatNest)
    A1, b1 = assembler.assemble(
        mat_type=dolfin.cpp.fem.Assembler.BlockType.nested)
    assert A1.mat().getType() == "nest"

    bnorm1 = math.sqrt(sum([x.norm()**2 for x in b1.vec().getNestSubVecs()]))
    assert bnorm0 == pytest.approx(bnorm1, 1.0e-12)

    try:
        Anorm1 = 0.0
        nrows, ncols = A1.mat().getNestSize()
        for row in range(nrows):
            for col in range(ncols):
                A_sub = A1.mat().getNestSubMatrix(row, col)
                norm = A_sub.norm()
                Anorm1 += norm * norm
                # A_sub.view()

        # is_rows, is_cols = A1.mat().getNestLocalISs()
        # for is0 in is_rows:
        #     for is1 in is_cols:
        #         A_sub = A1.mat().getLocalSubMatrix(is0, is1)
        #         norm = A_sub.norm()
        #         Anorm1 += norm * norm

        Anorm1 = math.sqrt(Anorm1)
        assert Anorm0 == pytest.approx(Anorm1, 1.0e-12)

    except AttributeError:
        print("Recent petsc4py(-dev) required to get MatNest sub-matrix.")

    # Monolithic version
    E = P0 * P1
    W = dolfin.function.functionspace.FunctionSpace(mesh, E)
    u0, u1 = dolfin.function.argument.TrialFunctions(W)
    v0, v1 = dolfin.function.argument.TestFunctions(W)
    a = u0 * v0 * dx + u1 * v1 * dx + u0 * v1 * dx + u1 * v0 * dx
    L = zero * f * v0 * ufl.dx + g * v1 * dx

    bc = dolfin.fem.dirichletbc.DirichletBC(W.sub(1), u_bc, boundary)
    assembler = dolfin.fem.assembling.Assembler([[a]], [L], [bc])

    A2, b2 = assembler.assemble(
        mat_type=dolfin.cpp.fem.Assembler.BlockType.monolithic)
    assert A2.mat().getType() != "nest"

    Anorm2 = A2.mat().norm()
    bnorm2 = b2.vec().norm()
    assert Anorm0 == pytest.approx(Anorm2, 1.0e-9)
    assert bnorm0 == pytest.approx(bnorm2, 1.0e-9)


def test_assembly_solve_block():

    mesh = dolfin.generation.UnitSquareMesh(dolfin.MPI.comm_world, 30, 31)
    p0, p1 = 1, 1
    P0 = ufl.FiniteElement("Lagrange", mesh.ufl_cell(), p0)
    P1 = ufl.FiniteElement("Lagrange", mesh.ufl_cell(), p1)
    V0 = dolfin.function.functionspace.FunctionSpace(mesh, P0)
    V1 = dolfin.function.functionspace.FunctionSpace(mesh, P1)

    def boundary(x):
        return numpy.logical_or(x[:, 0] < 1.0e-6, x[:, 0] > 1.0 - 1.0e-6)

    u_bc0 = dolfin.function.constant.Constant(50.0)
    u_bc1 = dolfin.function.constant.Constant(20.0)
    bc0 = dolfin.fem.dirichletbc.DirichletBC(V0, u_bc0, boundary)
    bc1 = dolfin.fem.dirichletbc.DirichletBC(V1, u_bc1, boundary)

    # Variational problem
    u, p = dolfin.function.argument.TrialFunction(
        V0), dolfin.function.argument.TrialFunction(V1)
    v, q = dolfin.function.argument.TestFunction(
        V0), dolfin.function.argument.TestFunction(V1)
    f = dolfin.function.constant.Constant(1.0)
    g = dolfin.function.constant.Constant(-3.0)
    zero = dolfin.function.constant.Constant(0.0)

    a00 = u * v * dx
    a01 = zero * v * p * dx
    a10 = zero * q * u * dx
    a11 = q * p * dx
    L0 = f * v * dx
    L1 = g * q * dx

    def monitor(ksp, its, rnorm):
        pass
        # print("Norm:", its, rnorm)

    # Create assembler
    assembler = dolfin.fem.assembling.Assembler([[a00, a01], [a10, a11]],
                                                [L0, L1], [bc0, bc1])

    # # Monolithic blocked
    A0, b0 = assembler.assemble(
        mat_type=dolfin.cpp.fem.Assembler.BlockType.monolithic)
    A0norm = A0.mat().norm()
    b0norm = b0.vec().norm()

    x0 = A0.mat().createVecLeft()
    ksp = PETSc.KSP()
    ksp.create(PETSc.COMM_WORLD)
    ksp.setOperators(A0.mat())
    ksp.setTolerances(rtol=1.0e-12)
    ksp.setMonitor(monitor)
    ksp.setType('cg')
    ksp.setFromOptions()
    ksp.view()
    ksp.solve(b0.vec(), x0)
    x0norm = x0.norm()

    # Nested (MatNest)
    A1, b1 = assembler.assemble(
        mat_type=dolfin.cpp.fem.Assembler.BlockType.nested)
    b1norm = b1.vec().norm()
    assert b0norm == pytest.approx(b1norm, 1.0e-12)

    x1 = dolfin.la.PETScVector(b1)
    ksp = PETSc.KSP()
    ksp.create(PETSc.COMM_WORLD)
    ksp.setMonitor(monitor)
    ksp.setTolerances(rtol=1.0e-12)
    ksp.setOperators(A1.mat())
    ksp.setType('cg')
    ksp.setFromOptions()
    ksp.view()
    ksp.solve(b1.vec(), x1.vec())
    x1norm = x1.vec().norm()

    assert x0norm == pytest.approx(x1norm, 1.0e-10)

    return

    # Monolithic version
    E = P0 * P1
    W = dolfin.function.functionspace.FunctionSpace(mesh, E)
    u0, u1 = dolfin.function.argument.TrialFunctions(W)
    v0, v1 = dolfin.function.argument.TestFunctions(W)
    a = u0 * v0 * dx + u1 * v1 * dx
    L = f * v0 * ufl.dx + g * v1 * dx

    # bc0 = dolfin.fem.dirichletbc.DirichletBC(W.sub(1), u_bc, boundary)
    # bc1 = dolfin.fem.dirichletbc.DirichletBC(W.sub(1), u_bc, boundary)
    u_bc = dolfin.function.constant.Constant((50.0, 20.0))
    bc = dolfin.fem.dirichletbc.DirichletBC(W, u_bc, boundary)
    assembler = dolfin.fem.assembling.Assembler([[a]], [L], [bc])

    A2, b2 = assembler.assemble(
        mat_type=dolfin.cpp.fem.Assembler.BlockType.monolithic)
    return
    A2norm = A2.mat().norm()
    b2norm = b2.vec().norm()

    x2 = dolfin.cpp.la.PETScVector(b2)
    ksp = PETSc.KSP()
    ksp.create(PETSc.COMM_WORLD)
    ksp.setMonitor(monitor)
    ksp.setOperators(A2.mat())
    ksp.setType('cg')
    ksp.setTolerances(rtol=1.0e-9)
    ksp.setFromOptions()
    ksp.view()
    ksp.solve(b2.vec(), x2.vec())
    x2norm = x2.vec().norm()
    print("---------- Done (2): ", x2norm)

    # solver2 = dolfin.la.PETScKrylovSolver(mesh.mpi_comm())
    # #solver2.set_options_prefix("test_lu_")
    # # dolfin.la.PETScOptions.set("test_lu_ksp_type", "preonly")
    # # dolfin.la.PETScOptions.set("test_lu_pc_type", "lu")
    # #dolfin.la.PETScOptions.set("ksp_type", "preonly")
    # dolfin.la.PETScOptions.set("pc_type", "lu")
    # dolfin.la.PETScOptions.set("ksp_view")
    # dolfin.la.PETScOptions.set("ksp_monitor_true_residual")

    # solver2.set_operator(A2)
    # solver2.set_from_options()

    # solver2.ksp().view()
    # solver2.ksp().monitor()

    # from petsc4py import PETSc
    # Monitor = PETSc.KSP.Monitor
    # solve.ksp().setMonitor(Monitor.TRUE_RESIDUAL_NORM)
    # self.ksp.setMonitor(Monitor.DEFAULT)
    # self.ksp.setMonitor(Monitor.TRUE_RESIDUAL_NORM)
    # self.ksp.setMonitor(Monitor.SOLUTION)

    # solver2.solve(x2, b2)

    # x2.vec().view()
