# Copyright (C) 2009-2023 Chris N. Richardson, Garth N. Wells and Michal Habera
#
# This file is part of DOLFINx (https://www.fenicsproject.org)
#
# SPDX-License-Identifier:    LGPL-3.0-or-later
"""Finite element function spaces and functions"""

from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from dolfinx.mesh import Mesh

from functools import singledispatch

import basix
import basix.ufl
import numpy as np
import numpy.typing as npt
import ufl
import ufl.algorithms
import ufl.algorithms.analysis
from dolfinx.fem import dofmap
from ufl.domain import extract_unique_domain

from dolfinx import cpp as _cpp
from dolfinx import default_scalar_type, jit, la


class Constant(ufl.Constant):
    def __init__(self, domain, c: typing.Union[np.ndarray, typing.Sequence, float, complex]):
        """A constant with respect to a domain.

        Args:
            domain: DOLFINx or UFL mesh
            c: Value of the constant.

        """
        c = np.asarray(c)
        super().__init__(domain, c.shape)
        try:
            if c.dtype == np.complex64:
                self._cpp_object = _cpp.fem.Constant_complex64(c)
            elif c.dtype == np.complex128:
                self._cpp_object = _cpp.fem.Constant_complex128(c)
            elif c.dtype == np.float32:
                self._cpp_object = _cpp.fem.Constant_float32(c)
            elif c.dtype == np.float64:
                self._cpp_object = _cpp.fem.Constant_float64(c)
            else:
                raise RuntimeError("Unsupported dtype")
        except AttributeError:
            raise AttributeError("Constant value must have a dtype attribute.")

    @property
    def value(self):
        """The value of the constant"""
        return self._cpp_object.value

    @value.setter
    def value(self, v):
        np.copyto(self._cpp_object.value, np.asarray(v))

    @property
    def dtype(self) -> np.dtype:
        return self._cpp_object.dtype

    def __float__(self):
        if self.ufl_shape or self.ufl_free_indices:
            raise TypeError(
                "Cannot evaluate a nonscalar expression to a scalar value.")
        else:
            return float(self.value)

    def __complex__(self):
        if self.ufl_shape or self.ufl_free_indices:
            raise TypeError(
                "Cannot evaluate a nonscalar expression to a scalar value.")
        else:
            return complex(self.value)


class Expression:
    def __init__(self, ufl_expression: ufl.core.expr.Expr, X: np.ndarray,
                 form_compiler_options: dict = {}, jit_options: dict = {},
                 dtype=default_scalar_type):
        """Create DOLFINx Expression.

        Represents a mathematical expression evaluated at a pre-defined
        set of points on the reference cell. This class closely follows
        the concept of a UFC Expression.

        This functionality can be used to evaluate a gradient of a
        Function at the quadrature points in all cells. This evaluated
        gradient can then be used as input to a non-FEniCS function that
        calculates a material constitutive model.

        Args:
            ufl_expression: Pure UFL expression
            X: Array of points of shape `(num_points, tdim)` on the
                reference element.
            form_compiler_options: Options used in FFCx compilation of
                this Expression. Run ``ffcx --help`` in the commandline
                to see all available options.
            jit_options: Options controlling JIT compilation of C code.

        Notes:
            This wrapper is responsible for the FFCx compilation of the
            UFL Expr and attaching the correct data to the underlying
            C++ Expression.

        """

        assert X.ndim < 3
        num_points = X.shape[0] if X.ndim == 2 else 1
        _X = np.reshape(X, (num_points, -1))

        mesh = extract_unique_domain(ufl_expression).ufl_cargo()

        # Compile UFL expression with JIT
        if dtype == np.float32:
            form_compiler_options["scalar_type"] = "float"
        if dtype == np.float64:
            form_compiler_options["scalar_type"] = "double"
        elif dtype == np.complex128:
            form_compiler_options["scalar_type"] = "double _Complex"
        else:
            raise RuntimeError(
                f"Unsupported scalar type {dtype} for Expression.")

        self._ufcx_expression, module, self._code = jit.ffcx_jit(mesh.comm, (ufl_expression, _X),
                                                                 form_compiler_options=form_compiler_options,
                                                                 jit_options=jit_options)
        self._ufl_expression = ufl_expression

        # Prepare coefficients data. For every coefficient in form take
        # its C++ object.
        original_coefficients = ufl.algorithms.extract_coefficients(
            ufl_expression)
        coeffs = [original_coefficients[self._ufcx_expression.original_coefficient_positions[i]]._cpp_object
                  for i in range(self._ufcx_expression.num_coefficients)]

        ufl_constants = ufl.algorithms.analysis.extract_constants(
            ufl_expression)
        constants = [constant._cpp_object for constant in ufl_constants]
        arguments = ufl.algorithms.extract_arguments(ufl_expression)
        if len(arguments) == 0:
            self._argument_function_space = None
        elif len(arguments) == 1:
            self._argument_function_space = arguments[0].ufl_function_space(
            )._cpp_object
        else:
            raise RuntimeError(
                "Expressions with more that one Argument not allowed.")

        def create_expression(dtype):
            if dtype is np.float32:
                return _cpp.fem.create_expression_float32
            elif dtype is np.float64:
                return _cpp.fem.create_expression_float64
            elif dtype is np.complex128:
                return _cpp.fem.create_expression_complex128
            else:
                raise NotImplementedError(f"Type {dtype} not supported.")

        ffi = module.ffi
        self._cpp_object = create_expression(dtype)(ffi.cast("uintptr_t", ffi.addressof(self._ufcx_expression)),
                                                    coeffs, constants, mesh, self.argument_function_space)

    def eval(self, cells: np.ndarray, values: typing.Optional[np.ndarray] = None) -> np.ndarray:
        """Evaluate Expression in cells. Values should have shape
        (cells.shape[0], num_points * value_size * num_all_argument_dofs).
        If values is not passed then a new array will be allocated.

        """
        _cells = np.asarray(cells, dtype=np.int32)
        if self.argument_function_space is None:
            argument_space_dimension = 1
        else:
            argument_space_dimension = self.argument_function_space.element.space_dimension
        values_shape = (_cells.shape[0], self.X(
        ).shape[0] * self.value_size * argument_space_dimension)

        # Allocate memory for result if u was not provided
        if values is None:
            values = np.zeros(values_shape, dtype=self.dtype)
        else:
            if values.shape != values_shape:
                raise TypeError(
                    "Passed array values does not have correct shape.")
            if values.dtype != self.dtype:
                raise TypeError(
                    "Passed array values does not have correct dtype.")

        self._cpp_object.eval(cells, values)

        return values

    def X(self) -> np.ndarray:
        """Evaluation points on the reference cell"""
        return self._cpp_object.X()

    @property
    def ufl_expression(self):
        """Original UFL Expression"""
        return self._ufl_expression

    @property
    def value_size(self) -> int:
        """Value size of the expression"""
        return self._cpp_object.value_size

    @property
    def argument_function_space(self) -> typing.Optional[FunctionSpace]:
        """The argument function space if expression has argument"""
        return self._argument_function_space

    @property
    def ufcx_expression(self):
        """The compiled ufcx_expression object"""
        return self._ufcx_expression

    @property
    def code(self) -> str:
        """C code strings"""
        return self._code

    @property
    def dtype(self) -> np.dtype:
        return self._cpp_object.dtype


class Function(ufl.Coefficient):
    """A finite element function that is represented by a function space
    (domain, element and dofmap) and a vector holding the
    degrees-of-freedom

    """

    def __init__(self, V: FunctionSpace, x: typing.Optional[la.VectorMetaClass] = None,
                 name: typing.Optional[str] = None, dtype: np.dtype = default_scalar_type):
        """Initialize a finite element Function.

        Args:
            V: The function space that the Function is defined on.
            x: Function degree-of-freedom vector. Typically required
                only when reading a saved Function from file.
            name: Function name.
            dtype: Scalar type.

        """

        # PETSc Vec wrapper around the C++ function data (constructed
        # when first requested)
        self._petsc_x = None

        # Create cpp Function
        def functiontype(dtype):
            if dtype == np.dtype(np.float32):
                return _cpp.fem.Function_float32
            elif dtype == np.dtype(np.float64):
                return _cpp.fem.Function_float64
            elif dtype == np.dtype(np.complex64):
                return _cpp.fem.Function_complex64
            elif dtype == np.dtype(np.complex128):
                return _cpp.fem.Function_complex128
            else:
                raise NotImplementedError(f"Type {dtype} not supported.")

        if x is not None:
            self._cpp_object = functiontype(dtype)(V._cpp_object, x)
        else:
            self._cpp_object = functiontype(dtype)(V._cpp_object)

        # Initialize the ufl.FunctionSpace
        super().__init__(V.ufl_function_space())

        # Set name
        if name is None:
            self.name = "f"
        else:
            self.name = name

        # Store DOLFINx FunctionSpace object
        self._V = V

    def __del__(self):
        if self._petsc_x is not None:
            self._petsc_x.destroy()

    @property
    def function_space(self) -> FunctionSpace:
        """The FunctionSpace that the Function is defined on"""
        return self._V

    def eval(self, x: npt.ArrayLike, cells: npt.ArrayLike, u=None) -> np.ndarray:
        """Evaluate Function at points x, where x has shape (num_points, 3),
        and cells has shape (num_points,) and cell[i] is the index of the
        cell containing point x[i]. If the cell index is negative the
        point is ignored."""

        # Make sure input coordinates are a NumPy array
        _x = np.asarray(x, dtype=np.float64)
        assert _x.ndim < 3
        if len(_x) == 0:
            _x = np.zeros((0, 3))
        else:
            shape0 = _x.shape[0] if _x.ndim == 2 else 1
            _x = np.reshape(_x, (shape0, -1))
        num_points = _x.shape[0]
        if _x.shape[1] != 3:
            raise ValueError("Coordinate(s) for Function evaluation must have length 3.")

        # Make sure cells are a NumPy array
        _cells = np.asarray(cells, dtype=np.int32)
        assert _cells.ndim < 2
        num_points_c = _cells.shape[0] if _cells.ndim == 1 else 1
        _cells = np.reshape(_cells, num_points_c)

        # Allocate memory for return value if not provided
        if u is None:
            value_size = ufl.product(self.ufl_element().value_shape())
            if np.issubdtype(default_scalar_type, np.complexfloating):
                u = np.empty((num_points, value_size), dtype=np.complex128)
            else:
                u = np.empty((num_points, value_size))

        self._cpp_object.eval(_x, _cells, u)
        if num_points == 1:
            u = np.reshape(u, (-1, ))
        return u

    def interpolate(self, u: typing.Union[typing.Callable, Expression, Function],
                    cells: typing.Optional[np.ndarray] = None,
                    nmm_interpolation_data=((), (), (), ())) -> None:
        """Interpolate an expression

        Args:
            u: The function, Expression or Function to interpolate.
            cells: The cells to interpolate over. If `None` then all
                cells are interpolated over.

        """
        @singledispatch
        def _interpolate(u, cells: typing.Optional[np.ndarray] = None):
            """Interpolate a cpp.fem.Function"""
            self._cpp_object.interpolate(u, cells, nmm_interpolation_data)

        @_interpolate.register(Function)
        def _(u: Function, cells: typing.Optional[np.ndarray] = None):
            """Interpolate a fem.Function"""
            self._cpp_object.interpolate(u._cpp_object, cells, nmm_interpolation_data)

        @_interpolate.register(int)
        def _(u_ptr: int, cells: typing.Optional[np.ndarray] = None):
            """Interpolate using a pointer to a function f(x)"""
            self._cpp_object.interpolate_ptr(u_ptr, cells)

        @_interpolate.register(Expression)
        def _(expr: Expression, cells: typing.Optional[np.ndarray] = None):
            """Interpolate Expression for the set of cells"""
            self._cpp_object.interpolate(expr._cpp_object, cells)

        if cells is None:
            mesh = self.function_space.mesh
            map = mesh.topology.index_map(mesh.topology.dim)
            cells = np.arange(map.size_local + map.num_ghosts, dtype=np.int32)

        try:
            # u is a Function or Expression (or pointer to one)
            _interpolate(u, cells)
        except TypeError:
            # u is callable
            assert callable(u)
            x = _cpp.fem.interpolation_coords(self._V.element, self._V.mesh.geometry, cells)
            self._cpp_object.interpolate(np.asarray(u(x), dtype=self.dtype), cells)

    def copy(self) -> Function:
        """Create a copy of the Function. The FunctionSpace is shared and the
        degree-of-freedom vector is copied.

        """
        return Function(self.function_space, type(self.x)(self.x))

    @property
    def vector(self):
        """PETSc vector holding the degrees-of-freedom."""
        if self._petsc_x is None:
            self._petsc_x = _cpp.la.petsc.create_vector_wrap(self.x)
        return self._petsc_x

    @property
    def x(self):
        """Vector holding the degrees-of-freedom."""
        return self._cpp_object.x

    @property
    def dtype(self) -> np.dtype:
        return self._cpp_object.x.array.dtype

    @property
    def name(self) -> str:
        """Name of the Function."""
        return self._cpp_object.name

    @name.setter
    def name(self, name):
        self._cpp_object.name = name

    def __str__(self):
        """Pretty print representation of it self."""
        return self.name

    def sub(self, i: int) -> Function:
        """Return a sub function.

        Args:
            i: The index of the sub-function to extract.

        Note:
            The sub functions are numbered i = 0..N-1, where N is the
            total number of sub spaces.

        """
        return Function(self._V.sub(i), self.x, name=f"{str(self)}_{i}")

    def split(self) -> tuple[Function, ...]:
        """Extract any sub functions.

        A sub function can be extracted from a discrete function that
        is in a mixed, vector, or tensor FunctionSpace. The sub
        function resides in the subspace of the mixed space.

        Args:
            Function space subspaces.

        """
        num_sub_spaces = self.function_space.num_sub_spaces
        if num_sub_spaces == 1:
            raise RuntimeError("No subfunctions to extract")
        return tuple(self.sub(i) for i in range(num_sub_spaces))

    def collapse(self) -> Function:
        u_collapsed = self._cpp_object.collapse()
        V_collapsed = FunctionSpace(self.function_space._mesh, self.ufl_element(), u_collapsed.function_space)
        return Function(V_collapsed, u_collapsed.x)


class ElementMetaData(typing.NamedTuple):
    """Data for representing a finite element"""
    family: str
    degree: int


class FunctionSpace(ufl.FunctionSpace):
    """A space on which Functions (fields) can be defined."""

    def __init__(self, mesh: Mesh,
                 element: typing.Union[ufl.FiniteElementBase, ElementMetaData, typing.Tuple[str, int]],
                 cppV: typing.Optional[_cpp.fem.FunctionSpace] = None,
                 form_compiler_options: dict[str, typing.Any] = {}, jit_options: dict[str, typing.Any] = {}):
        """Create a finite element function space."""

        if cppV is None:
            # Initialise the ufl.FunctionSpace
            try:
                # UFL element
                super().__init__(mesh.ufl_domain(), element)
            except BaseException:
                assert len(element) == 2, "Expected sequence of (element_type, degree)"
                e = ElementMetaData(*element)
                ufl_e = basix.ufl.element(
                    e.family, mesh.basix_cell(), e.degree, gdim=mesh.ufl_cell().geometric_dimension())
                super().__init__(mesh.ufl_domain(), ufl_e)

            # Compile dofmap and element and create DOLFIN objects
            (self._ufcx_element, self._ufcx_dofmap), module, code = jit.ffcx_jit(
                mesh.comm, self.ufl_element(), form_compiler_options=form_compiler_options,
                jit_options=jit_options)

            ffi = module.ffi
            cpp_element = _cpp.fem.FiniteElement(ffi.cast("uintptr_t", ffi.addressof(self._ufcx_element)))
            cpp_dofmap = _cpp.fem.create_dofmap(mesh.comm, ffi.cast(
                "uintptr_t", ffi.addressof(self._ufcx_dofmap)), mesh.topology, cpp_element)

            # Initialize the cpp.FunctionSpace and store mesh
            self._cpp_object = _cpp.fem.FunctionSpace(mesh._cpp_object, cpp_element, cpp_dofmap)
            self._mesh = mesh
        else:
            # Create function space from a UFL element and an existing
            # C++ FunctionSpace
            if mesh._cpp_object is not cppV.mesh:
                raise RecursionError("Meshes do not match in FunctionSpace initialisation.")
            ufl_domain = mesh.ufl_domain()
            super().__init__(ufl_domain, element)
            self._cpp_object = cppV
            self._mesh = mesh
            return

    def clone(self) -> FunctionSpace:
        """Create a new FunctionSpace :math:`W` which shares data with this
        FunctionSpace :math:`V`, but with a different unique integer ID.

        This function is helpful for defining mixed problems and using
        blocked linear algebra. For example, a matrix block defined on
        the spaces :math:`V \\times W` where, :math:`V` and :math:`W`
        are defined on the same finite element and mesh can be
        identified as an off-diagonal block whereas the :math:`V \\times
        V` and :math:`V \\times V` matrices can be identified as
        diagonal blocks. This is relevant for the handling of boundary
        conditions.

        Returns:
            A new function space that shares data

        """
        Vcpp = _cpp.fem.FunctionSpace(self._cpp_object.mesh, self._cpp_object.element, self._cpp_object.dofmap)
        return FunctionSpace(self._mesh, self.ufl_element(), Vcpp)

    @property
    def num_sub_spaces(self) -> int:
        """Number of sub spaces"""
        return self.element.num_sub_elements

    def sub(self, i: int) -> FunctionSpace:
        """Return the i-th sub space.

        Args:
            i: The subspace index

        Returns:
            A subspace

        """
        assert self.ufl_element().num_sub_elements() > i
        sub_element = self.ufl_element().sub_elements()[i]
        cppV_sub = self._cpp_object.sub([i])
        return FunctionSpace(self._mesh, sub_element, cppV_sub)

    def component(self):
        """Return the component relative to the parent space."""
        return self._cpp_object.component()

    def contains(self, V) -> bool:
        """Check if a space is contained in, or is the same as (identity), this space.

        Args:
            V: The space to check to for inclusion.

        Returns:
            True is ``V`` is contained in, or is the same as, this space

        """
        return self._cpp_object.contains(V._cpp_object)

    def __eq__(self, other):
        """Comparison for equality."""
        return super().__eq__(other) and self._cpp_object == other._cpp_object

    def __ne__(self, other):
        """Comparison for inequality."""
        return super().__ne__(other) or self._cpp_object != other._cpp_object

    def ufl_function_space(self) -> ufl.FunctionSpace:
        """UFL function space"""
        return self

    @property
    def element(self):
        """Function space finite element."""
        return self._cpp_object.element

    @property
    def dofmap(self) -> dofmap.DofMap:
        """Degree-of-freedom map associated with the function space."""
        return dofmap.DofMap(self._cpp_object.dofmap)

    @property
    def mesh(self) -> Mesh:
        """Mesh on which the function space is defined."""
        return self._mesh

    def collapse(self) -> tuple[FunctionSpace, np.ndarray]:
        """Collapse a subspace and return a new function space and a map from
        new to old dofs.

        Returns:
            The new function space and the map from new to old degrees-of-freedom.

        """
        cpp_space, dofs = self._cpp_object.collapse()
        V = FunctionSpace(self._mesh, self.ufl_element(), cpp_space)
        return V, dofs

    def tabulate_dof_coordinates(self) -> npt.NDArray[np.float64]:
        """Tabulate the coordinates of the degrees-of-freedom in the function space.

            Returns:
                Coordinates of the degrees-of-freedom.

            Notes:
                This method should be used only for elements with point
                evaluation degrees-of-freedom.

         """
        return self._cpp_object.tabulate_dof_coordinates()


def _is_scalar(mesh, element):
    try:
        e = basix.ufl.element(element.family(), element.cell_type,         # type: ignore
                              element.degree(), element.lagrange_variant,  # type: ignore
                              element.dpc_variant, element.discontinuous,  # type: ignore
                              gdim=mesh.geometry.dim)
    except AttributeError:
        ed = ElementMetaData(*element)
        e = basix.ufl.element(ed.family, mesh.basix_cell(), ed.degree,
                              gdim=mesh.geometry.dim)
    return len(e.value_shape()) == 0


def VectorFunctionSpace(mesh: Mesh,
                        element: typing.Union[basix.ufl._ElementBase,
                                              ElementMetaData, typing.Tuple[str, int]],
                        dim=None) -> FunctionSpace:
    """Create vector finite element (composition of scalar elements) function space."""
    if not _is_scalar(mesh, element):
        raise ValueError("Cannot create vector element containing a non-scalar.")

    try:
        ufl_e = basix.ufl.element(element.family(), element.cell_type,         # type: ignore
                                  element.degree(), element.lagrange_variant,  # type: ignore
                                  element.dpc_variant, element.discontinuous,  # type: ignore
                                  shape=(mesh.geometry.dim, ) if dim is None else (dim, ),
                                  gdim=mesh.geometry.dim, rank=1)
    except AttributeError:
        ed = ElementMetaData(*element)
        ufl_e = basix.ufl.element(ed.family, mesh.basix_cell(), ed.degree,
                                  shape=(mesh.geometry.dim, ) if dim is None else (dim, ),
                                  gdim=mesh.geometry.dim, rank=1)
    return FunctionSpace(mesh, ufl_e)


def TensorFunctionSpace(mesh: Mesh, element: typing.Union[ElementMetaData, typing.Tuple[str, int]], shape=None,
                        symmetry: typing.Optional[bool] = None) -> FunctionSpace:
    """Create tensor finite element (composition of scalar elements) function space."""
    if not _is_scalar(mesh, element):
        raise ValueError("Cannot create tensor element containing a non-scalar.")

    e = ElementMetaData(*element)
    gdim = mesh.geometry.dim
    shape_ = (gdim, gdim) if shape is None else shape
    ufl_element = basix.ufl.element(e.family, mesh.basix_cell(),
                                    e.degree, shape=shape_, symmetry=symmetry,
                                    gdim=mesh.geometry.dim, rank=2)
    return FunctionSpace(mesh, ufl_element)
